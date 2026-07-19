"""Composition root for API and worker processes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import uuid4

from document_intelligence.answering.service import AnswerService
from document_intelligence.application import DocumentApplication
from document_intelligence.config import Settings, get_settings
from document_intelligence.database import Database
from document_intelligence.ingestion.nodes import LlamaIndexNodeBuilder
from document_intelligence.ingestion.pipeline import IngestionPipeline
from document_intelligence.ingestion.upload import UploadService
from document_intelligence.jobs import (
    JobCoordinator,
    VerifiedDeletionCoordinator,
)
from document_intelligence.parsers import PDFParser, PDFParserOptions
from document_intelligence.parsers.ocr import OCRProcessor
from document_intelligence.providers import (
    DeterministicAnswerProvider,
    DeterministicEmbeddingProvider,
    DeterministicVisualProvider,
    OpenAIAnswerProvider,
    OpenAIEmbeddingProvider,
    OpenAIVisualProvider,
    image_data_url,
)
from document_intelligence.providers.base import (
    AnswerProvider,
    EmbeddingProvider,
    VisualUnderstandingProvider,
)
from document_intelligence.repository import Repository
from document_intelligence.retrieval.index import ChromaVectorIndex
from document_intelligence.retrieval.models import RetrievedEvidence
from document_intelligence.retrieval.retriever import HybridRetriever
from document_intelligence.sample import SAMPLE_PATH
from document_intelligence.storage import FileStorage
from document_intelligence.worker import WorkerRunner

DEFAULT_WORKSPACE_ID = "workspace_default"
PARSER_SCHEMA_VERSION = "pdfplumber-pdfium2-v1"


class _VectorDeletionAdapter:
    def __init__(self, index: ChromaVectorIndex) -> None:
        self.index = index

    def delete_version(self, *, workspace_id: str, document_id: str, version_id: str) -> None:
        del document_id
        self.index.delete_version(workspace_id, version_id)

    def version_exists(self, *, workspace_id: str, document_id: str, version_id: str) -> bool:
        del document_id
        return self.index.count_version(workspace_id, version_id) > 0


@dataclass(slots=True)
class Runtime:
    """Fully wired local runtime; each process builds its own lightweight handles."""

    settings: Settings
    repository: Repository
    storage: FileStorage
    vector_index: ChromaVectorIndex
    ingestion: IngestionPipeline
    deletion: VerifiedDeletionCoordinator
    application: DocumentApplication


def create_runtime(settings: Settings | None = None) -> Runtime:
    configured = settings or get_settings()
    configured.ensure_directories()
    repository = Repository(
        Database(configured.database_path),
        lease_seconds=configured.worker_lease_seconds,
        max_attempts=configured.worker_max_attempts,
    )
    repository.initialize()
    repository.ensure_workspace(DEFAULT_WORKSPACE_ID, name="My documents")
    storage = FileStorage(configured.uploads_dir, configured.artifacts_dir)
    storage.initialize()

    ocr = OCRProcessor(
        enabled=configured.enable_ocr,
        timeout_seconds=configured.ocr_timeout_seconds,
    )
    embedding = _embedding_provider(configured)
    visual = _visual_provider(configured, ocr)
    answer_provider = _answer_provider(configured)
    parser_profile = _parser_profile(configured, visual)
    parser = PDFParser(
        PDFParserOptions(
            max_file_bytes=configured.max_file_bytes,
            max_pages=configured.max_pages,
            render_scale=configured.page_render_scale,
            enable_ocr=configured.enable_ocr,
            ocr_timeout_seconds=configured.ocr_timeout_seconds,
        ),
        ocr_processor=ocr,
    )
    vector_index = ChromaVectorIndex(
        configured.chroma_dir,
        embedding,
        parser_profile=parser_profile,
        embedding_profile=embedding.profile,
    )
    uploads = UploadService(
        repository,
        storage,
        parser,
        parser_profile=parser_profile,
        embedding_profile=embedding.profile,
        max_file_bytes=configured.max_file_bytes,
    )
    ingestion = IngestionPipeline(
        repository,
        storage,
        parser,
        vector_index,
        node_builder=LlamaIndexNodeBuilder(),
        visual_provider=visual,
        max_artifact_bytes=configured.max_file_bytes,
    )
    retriever = HybridRetriever(vector_index)
    answers = AnswerService(
        retriever,
        answer_provider,
        repository=repository,
        evidence_resolver=repository,
        asset_url_builder=_asset_url,
        asset_data_url_builder=(
            _asset_data_url_builder(storage) if configured.provider_mode == "openai" else None
        ),
    )
    application = DocumentApplication(
        settings=configured,
        workspace_id=DEFAULT_WORKSPACE_ID,
        repository=repository,
        storage=storage,
        uploads=uploads,
        answers=answers,
        ocr_processor=ocr,
        sample_path=SAMPLE_PATH,
    )
    deletion = VerifiedDeletionCoordinator(
        repository,
        storage,
        _VectorDeletionAdapter(vector_index),
    )
    return Runtime(
        settings=configured,
        repository=repository,
        storage=storage,
        vector_index=vector_index,
        ingestion=ingestion,
        deletion=deletion,
        application=application,
    )


def create_services(settings: Settings | None = None) -> DocumentApplication:
    """Default lazy FastAPI service factory."""

    return create_runtime(settings).application


def create_worker() -> WorkerRunner:
    """Default durable worker factory used by the CLI."""

    runtime = create_runtime()
    coordinator = JobCoordinator(
        runtime.repository,
        owner=f"worker-{uuid4().hex[:16]}",
        lease_seconds=runtime.settings.worker_lease_seconds,
    )
    return WorkerRunner(
        coordinator,
        ingestion_executor=runtime.ingestion,
        deletion_executor=runtime.deletion,
        poll_seconds=runtime.settings.worker_poll_seconds,
        max_attempts=runtime.settings.worker_max_attempts,
    )


def _embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "deterministic":
        return DeterministicEmbeddingProvider(settings.embedding_dimensions)
    return OpenAIEmbeddingProvider(
        api_key=settings.openai_api_key,
        model=settings.openai_embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.provider_timeout_seconds,
        max_attempts=settings.provider_max_attempts,
    )


def _visual_provider(settings: Settings, ocr: OCRProcessor) -> VisualUnderstandingProvider:
    if settings.provider_mode == "deterministic":
        return DeterministicVisualProvider(ocr_processor=ocr if settings.enable_ocr else None)
    return OpenAIVisualProvider(
        api_key=settings.openai_api_key,
        model=settings.openai_vision_model,
        timeout_seconds=settings.provider_timeout_seconds,
        max_attempts=settings.provider_max_attempts,
    )


def _answer_provider(settings: Settings) -> AnswerProvider:
    if settings.provider_mode == "deterministic":
        return DeterministicAnswerProvider()
    return OpenAIAnswerProvider(
        api_key=settings.openai_api_key,
        model=settings.openai_chat_model,
        timeout_seconds=settings.provider_timeout_seconds,
        max_attempts=settings.provider_max_attempts,
    )


def _parser_profile(settings: Settings, visual_provider: VisualUnderstandingProvider) -> str:
    render_scale = format(settings.page_render_scale, ".3g")
    return (
        f"{PARSER_SCHEMA_VERSION}-render{render_scale}-"
        f"ocr{int(settings.enable_ocr)}-{visual_provider.profile}"
    )[:160]


def _asset_url(hit: RetrievedEvidence) -> str | None:
    if not hit.asset_key:
        return None
    return f"/api/v1/assets/{hit.asset_key}"


def _asset_data_url_builder(
    storage: FileStorage,
) -> Callable[[RetrievedEvidence], str | None]:
    def build(hit: RetrievedEvidence) -> str | None:
        if not hit.asset_key:
            return None
        try:
            content = storage.read_artifact(hit.asset_key)
        except (OSError, ValueError):
            return None
        mime_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(PurePosixPath(hit.asset_key).suffix.casefold())
        if mime_type is None:
            return None
        return image_data_url(content, mime_type)

    return build
