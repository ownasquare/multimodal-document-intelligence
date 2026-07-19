"""Atomic upload acceptance across managed files and repository metadata."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import BinaryIO, Protocol

from document_intelligence.models import IngestionJob, UploadReceipt
from document_intelligence.parsers.base import PDFInfo
from document_intelligence.repository import Repository
from document_intelligence.storage import FileStorage, StorageDeletionError, sanitize_display_name


class PDFValidator(Protocol):
    def validate(self, source_path: Path) -> PDFInfo: ...


class UploadRollbackError(RuntimeError):
    """Upload acceptance failed and managed-file cleanup could not be proven."""


UploadData = bytes | bytearray | memoryview | BinaryIO | Iterable[bytes]


class UploadService:
    """Validate bytes before atomically accepting their durable metadata."""

    def __init__(
        self,
        repository: Repository,
        storage: FileStorage,
        validator: PDFValidator,
        *,
        parser_profile: str,
        embedding_profile: str,
        max_file_bytes: int,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        self.repository = repository
        self.storage = storage
        self.validator = validator
        self.parser_profile = parser_profile
        self.embedding_profile = embedding_profile
        self.max_file_bytes = max_file_bytes
        self.id_factory = id_factory or repository.new_id

    def accept(
        self,
        workspace_id: str,
        *,
        display_name: str,
        data: UploadData,
        mime_type: str = "application/pdf",
        document_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> UploadReceipt:
        """Store, validate, and accept a PDF; prove cleanup on every rejected path."""

        if mime_type != "application/pdf":
            raise ValueError("only application/pdf uploads are accepted")
        version_id = self.id_factory()
        stored = self.storage.store_upload(
            workspace_id,
            version_id,
            data,
            mime_type=mime_type,
            max_bytes=self.max_file_bytes,
        )
        try:
            info = self.validator.validate(stored.path)
            if info.byte_size != stored.byte_size:
                raise ValueError("validated PDF size does not match managed storage")
            receipt = self.repository.accept_upload(
                workspace_id,
                display_name=sanitize_display_name(display_name),
                sha256=stored.sha256,
                byte_size=stored.byte_size,
                mime_type=mime_type,
                source_key=stored.key,
                parser_profile=self.parser_profile,
                embedding_profile=self.embedding_profile,
                document_id=document_id,
                version_id=version_id,
                idempotency_key=idempotency_key,
            )
        except Exception:
            self._rollback(workspace_id, version_id)
            raise
        if receipt.version.id != version_id:
            self._rollback(workspace_id, version_id)
        return receipt

    def reprocess_document(self, document_id: str) -> IngestionJob:
        """Idempotently queue reprocessing of the current immutable version."""

        return self.repository.queue_reprocess(document_id)

    def _rollback(self, workspace_id: str, version_id: str) -> None:
        try:
            report = self.storage.delete_version(workspace_id, version_id)
        except StorageDeletionError as exc:
            raise UploadRollbackError("Rejected upload cleanup could not be verified.") from exc
        if not report.verified:
            raise UploadRollbackError("Rejected upload cleanup could not be verified.")
