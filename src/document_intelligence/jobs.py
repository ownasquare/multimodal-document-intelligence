"""Worker-facing job leases and verified document-deletion orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Self

from document_intelligence.models import IngestionJob, JobKind, JobStage
from document_intelligence.repository import DeletionReadback, Repository
from document_intelligence.storage import FileStorage, StorageDeletionError


class JobExecutionError(RuntimeError):
    """A stable, sanitized processing failure safe to persist and expose."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable


class DeletionVerificationError(JobExecutionError):
    """Deletion stopped because vector, file, or metadata readback was incomplete."""

    def __init__(self, message: str = "Document deletion could not be verified.") -> None:
        super().__init__("deletion_not_verified", message, retryable=True)


class VectorDeletionPort(Protocol):
    """Narrow adapter required from the vector-index service for safe deletion."""

    def delete_version(self, *, workspace_id: str, document_id: str, version_id: str) -> None: ...

    def version_exists(self, *, workspace_id: str, document_id: str, version_id: str) -> bool: ...


@dataclass(slots=True)
class JobLease:
    """One worker-owned durable lease; every mutation verifies current ownership."""

    repository: Repository
    owner: str
    job: IngestionJob
    lease_seconds: int

    def heartbeat(self) -> Self:
        self.job = self.repository.heartbeat_job(
            self.job.id, self.owner, lease_seconds=self.lease_seconds
        )
        return self

    def advance(self, stage: JobStage, progress: float) -> Self:
        self.job = self.repository.advance_job(
            self.job.id, self.owner, stage=stage, progress=progress
        )
        return self

    @property
    def cancellation_requested(self) -> bool:
        return self.repository.job_cancellation_requested(self.job.id, self.owner)

    def acknowledge_cancellation(self) -> IngestionJob:
        self.job = self.repository.acknowledge_job_cancellation(self.job.id, self.owner)
        return self.job

    def complete(self) -> IngestionJob:
        self.job = self.repository.complete_job(self.job.id, self.owner)
        return self.job

    def fail(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        retry_delay_seconds: float = 0,
    ) -> IngestionJob:
        self.job = self.repository.fail_job(
            self.job.id,
            self.owner,
            error_code=code,
            error_message=message,
            retryable=retryable,
            retry_delay_seconds=retry_delay_seconds,
        )
        return self.job

    def fail_error(
        self, error: JobExecutionError, *, retry_delay_seconds: float = 0
    ) -> IngestionJob:
        return self.fail(
            code=error.code,
            message=error.public_message,
            retryable=error.retryable,
            retry_delay_seconds=retry_delay_seconds,
        )


class JobCoordinator:
    """Bounded worker facade over repository-owned atomic lease operations."""

    def __init__(
        self,
        repository: Repository,
        *,
        owner: str,
        lease_seconds: int = 120,
    ) -> None:
        if not owner.strip():
            raise ValueError("owner must not be empty")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.repository = repository
        self.owner = owner
        self.lease_seconds = lease_seconds

    def recover(self) -> int:
        return self.repository.recover_expired_jobs()

    def lease(self, *, kinds: tuple[JobKind, ...] | None = None) -> JobLease | None:
        job = self.repository.lease_next_job(
            self.owner,
            lease_seconds=self.lease_seconds,
            kinds=kinds,
        )
        if job is None:
            return None
        return JobLease(
            repository=self.repository,
            owner=self.owner,
            job=job,
            lease_seconds=self.lease_seconds,
        )


class VerifiedDeletionCoordinator:
    """Delete vectors and managed files before making a tombstone successful."""

    def __init__(
        self,
        repository: Repository,
        storage: FileStorage,
        vector_index: VectorDeletionPort,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.vector_index = vector_index

    def execute(self, lease: JobLease) -> tuple[IngestionJob, DeletionReadback]:
        """Execute an idempotent deletion job with readback at every boundary."""

        if lease.job.kind is not JobKind.DELETE:
            raise ValueError("verified deletion requires a delete job lease")
        document = self.repository.get_document(lease.job.document_id, include_deleted=True)
        if document is None:
            raise DeletionVerificationError("The document deletion target no longer exists.")
        versions = self.repository.list_document_versions(document.id)
        lease.advance(JobStage.DELETING, 0.15)

        for version in versions:
            self.vector_index.delete_version(
                workspace_id=document.workspace_id,
                document_id=document.id,
                version_id=version.id,
            )
        lease.heartbeat()
        lease.advance(JobStage.VERIFYING, 0.55)
        if any(
            self.vector_index.version_exists(
                workspace_id=document.workspace_id,
                document_id=document.id,
                version_id=version.id,
            )
            for version in versions
        ):
            raise DeletionVerificationError("Vector records remain for this document.")

        try:
            reports = self.storage.delete_document(
                document.workspace_id, [version.id for version in versions]
            )
        except StorageDeletionError as exc:
            raise DeletionVerificationError(
                "Managed document files remain after deletion."
            ) from exc
        if not all(report.verified for report in reports):
            raise DeletionVerificationError("Managed document files remain after deletion.")
        lease.heartbeat()
        lease.advance(JobStage.VERIFYING, 0.9)
        readback = self.repository.finalize_document_deletion(
            document.id,
            artifacts_verified=True,
            vectors_verified=True,
        )
        if not readback.verified:
            raise DeletionVerificationError("Active document metadata remains after deletion.")
        return lease.complete(), readback


def classify_job_error(error: BaseException) -> JobExecutionError:
    """Map internal exceptions to stable codes without persisting raw exception text."""

    if isinstance(error, JobExecutionError):
        return error
    if isinstance(error, TimeoutError):
        return JobExecutionError(
            "operation_timeout",
            "A document processing step timed out.",
            retryable=True,
        )
    if isinstance(error, OSError):
        return JobExecutionError(
            "storage_unavailable",
            "Document storage was temporarily unavailable.",
            retryable=True,
        )
    return JobExecutionError(
        "processing_failed",
        "The document could not be processed.",
        retryable=False,
    )
