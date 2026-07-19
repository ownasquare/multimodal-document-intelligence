"""Durable ingestion orchestration from immutable PDF to verified vectors."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from llama_index.core.schema import TextNode

from document_intelligence.ingestion.nodes import LlamaIndexNodeBuilder
from document_intelligence.jobs import JobExecutionError, JobLease, classify_job_error
from document_intelligence.models import ContentElement, IngestionJob, JobKind, JobStage, Modality
from document_intelligence.parsers.base import (
    ParsedDocument,
    ParsedElement,
    ParserError,
)
from document_intelligence.providers.base import ProviderError, VisualUnderstandingProvider
from document_intelligence.repository import RecordNotFoundError, Repository
from document_intelligence.retrieval.index import IndexCompatibilityError
from document_intelligence.storage import FileStorage, StorageIntegrityError, StoredFile


class DocumentParser(Protocol):
    def parse(self, source_path: Path, *, artifact_dir: Path) -> ParsedDocument: ...


class VectorIndex(Protocol):
    def upsert(self, nodes: Sequence[TextNode]) -> int: ...

    def delete_version(self, workspace_id: str, version_id: str) -> int: ...

    def count_version(self, workspace_id: str, version_id: str) -> int: ...


@dataclass(frozen=True, slots=True)
class IngestionResult:
    job: IngestionJob
    element_count: int
    node_count: int


class IngestionPipeline:
    """Process one owned ingest/reprocess lease and fail closed on partial state."""

    def __init__(
        self,
        repository: Repository,
        storage: FileStorage,
        parser: DocumentParser,
        vector_index: VectorIndex,
        *,
        node_builder: LlamaIndexNodeBuilder | None = None,
        visual_provider: VisualUnderstandingProvider | None = None,
        max_artifact_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        if max_artifact_bytes < 1:
            raise ValueError("max_artifact_bytes must be positive")
        self.repository = repository
        self.storage = storage
        self.parser = parser
        self.vector_index = vector_index
        self.node_builder = node_builder or LlamaIndexNodeBuilder()
        self.visual_provider = visual_provider
        self.max_artifact_bytes = max_artifact_bytes

    def process(self, lease: JobLease) -> IngestionResult:
        """Run the staged pipeline and return the durable terminal job state."""

        if lease.job.kind not in {JobKind.INGEST, JobKind.REPROCESS}:
            raise ValueError("ingestion pipeline requires an ingest or reprocess lease")
        version = self.repository.get_document_version(lease.job.version_id)
        document = self.repository.get_document(lease.job.document_id, include_deleted=True)
        if version is None or document is None:
            error = JobExecutionError(
                "ingestion_target_missing",
                "The document version no longer exists.",
                retryable=False,
            )
            return IngestionResult(lease.fail_error(error), 0, 0)
        if (
            version.document_id != lease.job.document_id
            or document.workspace_id != lease.job.workspace_id
            or version.id != document.current_version_id
        ):
            error = JobExecutionError(
                "ingestion_scope_invalid",
                "The document version is no longer the active processing target.",
                retryable=False,
            )
            return IngestionResult(lease.fail_error(error), 0, 0)

        element_count = 0
        node_count = 0
        try:
            lease.advance(JobStage.READING, 0.05)
            self._reset_retry_state(lease.job.workspace_id, version.id)
            source_path = self.storage.resolve_upload_key(version.source_key)
            lease.advance(JobStage.EXTRACTING_TEXT, 0.15)
            with tempfile.TemporaryDirectory(prefix="docintel-parse-") as temporary_name:
                temporary_dir = Path(temporary_name)
                parsed = self.parser.parse(source_path, artifact_dir=temporary_dir)
                if parsed.sha256 != version.sha256:
                    raise StorageIntegrityError(
                        "immutable source digest changed after upload acceptance"
                    )
                lease.heartbeat()
                lease.advance(JobStage.EXTRACTING_TABLES, 0.35)
                lease.advance(JobStage.OCR, 0.45)
                assets = self._import_assets(
                    parsed,
                    workspace_id=lease.job.workspace_id,
                    version_id=version.id,
                    artifact_root=temporary_dir,
                )
                lease.advance(JobStage.UNDERSTANDING_VISUALS, 0.55)
                elements = self._content_elements(
                    parsed,
                    workspace_id=lease.job.workspace_id,
                    document_id=lease.job.document_id,
                    version_id=version.id,
                    assets=assets,
                )
                lease.heartbeat()

            self.repository.update_version_analysis(
                version.id,
                page_count=parsed.page_count,
                warning_count=len(parsed.warnings),
            )
            element_count = self.repository.replace_elements(version.id, elements)
            if len(self.repository.list_elements(version.id)) != element_count:
                raise JobExecutionError(
                    "element_readback_failed",
                    "Parsed evidence could not be verified after persistence.",
                    retryable=True,
                )

            lease.advance(JobStage.INDEXING, 0.72)
            nodes = self.node_builder.build(
                elements,
                parser_profile=version.parser_profile,
                embedding_profile=version.embedding_profile,
                document_names={document.id: document.display_name},
            )
            node_count = len(nodes)
            written = self.vector_index.upsert(nodes)
            if written != node_count:
                raise JobExecutionError(
                    "vector_write_incomplete",
                    "The searchable evidence inventory was incomplete.",
                    retryable=True,
                )
            lease.advance(JobStage.VERIFYING, 0.9)
            indexed = self.vector_index.count_version(lease.job.workspace_id, version.id)
            if indexed != node_count:
                raise JobExecutionError(
                    "vector_readback_failed",
                    "The searchable evidence inventory could not be verified.",
                    retryable=True,
                )
            completed = lease.complete()
            return IngestionResult(completed, element_count, node_count)
        except Exception as exc:
            cleanup_error = self._cleanup_partial(lease.job.workspace_id, version.id)
            if cleanup_error is not None:
                error = JobExecutionError(
                    "ingestion_cleanup_failed",
                    "Partial document processing state could not be cleared safely.",
                    retryable=True,
                )
            else:
                error = _classify_ingestion_error(exc)
            return IngestionResult(lease.fail_error(error), 0, 0)

    def _reset_retry_state(self, workspace_id: str, version_id: str) -> None:
        self.vector_index.delete_version(workspace_id, version_id)
        self.repository.replace_elements(version_id, [])

    def _cleanup_partial(self, workspace_id: str, version_id: str) -> Exception | None:
        try:
            self.vector_index.delete_version(workspace_id, version_id)
            self.repository.replace_elements(version_id, [])
        except Exception as exc:
            return exc
        return None

    def _import_assets(
        self,
        parsed: ParsedDocument,
        *,
        workspace_id: str,
        version_id: str,
        artifact_root: Path,
    ) -> dict[Path, StoredFile]:
        referenced: set[Path] = {
            path.resolve()
            for page in parsed.pages
            for path in ([page.page_asset_path] + [element.asset_path for element in page.elements])
            if path is not None
        }
        imported: dict[Path, StoredFile] = {}
        root = artifact_root.resolve()
        for ordinal, path in enumerate(sorted(referenced), start=1):
            try:
                relative = path.relative_to(root)
            except ValueError as exc:
                raise StorageIntegrityError(
                    "parser returned an asset outside its isolated artifact directory"
                ) from exc
            if not path.is_file() or path.is_symlink():
                raise StorageIntegrityError("parser returned an invalid derived asset")
            suffix = path.suffix.casefold()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise StorageIntegrityError("parser returned an unsupported asset type")
            asset_digest = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()[:20]
            with path.open("rb") as source:
                imported[path] = self.storage.store_artifact(
                    workspace_id,
                    version_id,
                    f"asset-{ordinal:05d}-{asset_digest}",
                    source,
                    suffix=".jpg" if suffix == ".jpeg" else suffix,
                    max_bytes=self.max_artifact_bytes,
                )
        return imported

    def _content_elements(
        self,
        parsed: ParsedDocument,
        *,
        workspace_id: str,
        document_id: str,
        version_id: str,
        assets: dict[Path, StoredFile],
    ) -> list[ContentElement]:
        page_assets = {
            page.page_number: assets[page.page_asset_path.resolve()]
            for page in parsed.pages
            if page.page_asset_path is not None
        }
        page_asset_paths = {
            page.page_number: page.page_asset_path.resolve()
            for page in parsed.pages
            if page.page_asset_path is not None
        }
        result: list[ContentElement] = []
        for ordinal, parsed_element in enumerate(parsed.elements):
            element = self._content_element(
                parsed_element,
                ordinal=ordinal,
                workspace_id=workspace_id,
                document_id=document_id,
                version_id=version_id,
                assets=assets,
                page_asset=page_assets.get(parsed_element.page_number),
                page_asset_path=page_asset_paths.get(parsed_element.page_number),
            )
            result.append(element)
            if (
                self.visual_provider is not None
                and element.modality in {Modality.IMAGE, Modality.CHART, Modality.DIAGRAM}
                and parsed_element.asset_path is not None
            ):
                result.append(
                    self._visual_description_element(
                        source=element,
                        asset_path=parsed_element.asset_path.resolve(),
                    )
                )
        return result

    def _content_element(
        self,
        parsed: ParsedElement,
        *,
        ordinal: int,
        workspace_id: str,
        document_id: str,
        version_id: str,
        assets: dict[Path, StoredFile],
        page_asset: StoredFile | None,
        page_asset_path: Path | None,
    ) -> ContentElement:
        modality = Modality(parsed.modality.value)
        direct_asset = assets.get(parsed.asset_path.resolve()) if parsed.asset_path else None
        metadata = dict(parsed.metadata)
        metadata.pop("asset_key", None)
        metadata["parser_modality"] = parsed.modality.value
        if page_asset:
            metadata["page_asset_key"] = page_asset.key
        if direct_asset:
            metadata["asset_sha256"] = direct_asset.sha256
            metadata["asset_byte_size"] = direct_asset.byte_size
        active_asset = direct_asset or page_asset
        original_path = parsed.asset_path.resolve() if parsed.asset_path else page_asset_path
        if original_path is not None:
            metadata["original_asset_filename"] = original_path.name
            metadata["asset_role"] = (
                "page_render" if original_path == page_asset_path else "element_crop"
            )
        identifier = _element_id(version_id, parsed, ordinal)
        return ContentElement(
            id=identifier,
            workspace_id=workspace_id,
            document_id=document_id,
            version_id=version_id,
            page_number=parsed.page_number,
            modality=modality,
            content=parsed.content,
            bbox=parsed.bbox,
            asset_key=active_asset.key if active_asset else None,
            confidence=parsed.confidence,
            extraction_method=parsed.extraction_method,
            metadata=metadata,
        )

    def _visual_description_element(
        self,
        *,
        source: ContentElement,
        asset_path: Path,
    ) -> ContentElement:
        if self.visual_provider is None:
            raise AssertionError("visual provider is required")
        mime_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }[asset_path.suffix.casefold()]
        description = self.visual_provider.describe(
            asset_path.read_bytes(),
            mime_type=mime_type,
            context=source.content,
            suggested_modality=source.modality,
        )
        digest = hashlib.sha256(
            f"{source.id}\x1f{self.visual_provider.profile}\x1f{description.summary}".encode()
        ).hexdigest()
        return ContentElement(
            id=f"element_{digest[:40]}",
            workspace_id=source.workspace_id,
            document_id=source.document_id,
            version_id=source.version_id,
            page_number=source.page_number,
            modality=description.modality,
            content=description.summary,
            bbox=source.bbox,
            asset_key=source.asset_key,
            confidence=min(source.confidence, description.confidence),
            extraction_method=self.visual_provider.profile[:80],
            metadata={
                "source_element_id": source.id,
                "observed_text": description.observed_text,
                "observed_facts": description.observed_facts,
                "provider_derived": True,
                "content_trust": "untrusted",
            },
        )


def _element_id(version_id: str, element: ParsedElement, ordinal: int) -> str:
    payload = json.dumps(
        {
            "version_id": version_id,
            "ordinal": ordinal,
            "page": element.page_number,
            "modality": element.modality.value,
            "bbox": element.bbox,
            "content": element.content,
            "method": element.extraction_method,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"element_{hashlib.sha256(payload.encode()).hexdigest()[:40]}"


def _classify_ingestion_error(error: Exception) -> JobExecutionError:
    if isinstance(error, JobExecutionError):
        return error
    if isinstance(error, ParserError):
        return JobExecutionError(
            error.code,
            "The PDF could not be parsed safely.",
            retryable=False,
        )
    if isinstance(error, ProviderError):
        return JobExecutionError(
            error.code,
            "Visual understanding was temporarily unavailable."
            if error.retryable
            else "Visual understanding could not process this document.",
            retryable=error.retryable,
        )
    if isinstance(error, (IndexCompatibilityError, StorageIntegrityError, ValueError)):
        return JobExecutionError(
            "ingestion_validation_failed",
            "Document processing produced incompatible evidence.",
            retryable=False,
        )
    if isinstance(error, RecordNotFoundError):
        return JobExecutionError(
            "ingestion_target_missing",
            "The document version no longer exists.",
            retryable=False,
        )
    return classify_job_error(error)
