"""Single-host leased worker loop with truthful heartbeat and shutdown behavior."""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Protocol

from document_intelligence.jobs import (
    JobCoordinator,
    JobExecutionError,
    JobLease,
    classify_job_error,
)
from document_intelligence.models import IngestionJob, JobKind, JobStatus
from document_intelligence.repository import LeaseLostError


class IngestionExecutorPort(Protocol):
    """Ingestion pipeline surface that finalizes its worker-owned lease."""

    def process(self, lease: JobLease) -> object:
        """Process an ingest/reprocess lease and finalize its durable status."""


class DeletionExecutorPort(Protocol):
    """Verified-deletion surface that finalizes its worker-owned lease."""

    def execute(self, lease: JobLease) -> object:
        """Delete and verify one document, then finalize the durable status."""


class WorkerFactoryPort(Protocol):
    """Factory surface implemented by ``document_intelligence.container``."""

    def __call__(self) -> WorkerRunner: ...


class LeaseHeartbeat:
    """Extend a synchronous executor's lease from a bounded daemon thread."""

    def __init__(self, lease: JobLease, *, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.lease = lease
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._error: Exception | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="document-intelligence-lease-heartbeat",
            daemon=True,
        )

    def __enter__(self) -> LeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds * 2))

    def raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.lease.heartbeat()
            except Exception as error:
                self._error = error
                self._stop.set()
                return


@contextmanager
def stop_on_signals(stop_event: threading.Event) -> Iterator[None]:
    """Translate SIGTERM/SIGINT into a graceful stop and restore prior handlers."""

    previous: dict[signal.Signals, signal._HANDLER] = {}

    def request_stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stop_event.set()

    try:
        for handled_signal in (signal.SIGTERM, signal.SIGINT):
            previous[handled_signal] = signal.getsignal(handled_signal)
            signal.signal(handled_signal, request_stop)
    except ValueError:
        previous.clear()
    try:
        yield
    finally:
        for handled_signal, handler in previous.items():
            signal.signal(handled_signal, handler)


class WorkerRunner:
    """Recover, lease, heartbeat, dispatch, and finalize one job at a time."""

    def __init__(
        self,
        coordinator: JobCoordinator,
        *,
        ingestion_executor: IngestionExecutorPort,
        deletion_executor: DeletionExecutorPort,
        poll_seconds: float = 0.5,
        max_attempts: int = 3,
        heartbeat_interval_seconds: float | None = None,
        stop_event: threading.Event | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        interval = heartbeat_interval_seconds or max(0.1, coordinator.lease_seconds / 3)
        if interval >= coordinator.lease_seconds:
            raise ValueError("heartbeat interval must be shorter than the lease")
        self.coordinator = coordinator
        self.ingestion_executor = ingestion_executor
        self.deletion_executor = deletion_executor
        self.poll_seconds = poll_seconds
        self.max_attempts = max_attempts
        self.heartbeat_interval_seconds = interval
        self.stop_event = stop_event or threading.Event()
        self.logger = logger or logging.getLogger(__name__)

    def request_stop(self) -> None:
        self.stop_event.set()

    def recover_stale_jobs(self) -> int:
        recovered = self.coordinator.recover()
        if recovered:
            self.logger.warning("Recovered %d expired job lease(s).", recovered)
        return recovered

    def run_once(self) -> IngestionJob | None:
        """Lease and synchronously execute at most one job."""

        lease = self.coordinator.lease()
        if lease is None:
            return None
        if lease.cancellation_requested:
            return lease.acknowledge_cancellation()
        heartbeat = LeaseHeartbeat(
            lease,
            interval_seconds=self.heartbeat_interval_seconds,
        )
        try:
            with heartbeat:
                self._dispatch(lease)
            if lease.job.status is JobStatus.RUNNING:
                heartbeat.raise_if_failed()
                raise JobExecutionError(
                    "executor_incomplete",
                    "The document processor did not finalize its job lease.",
                    retryable=False,
                )
            return lease.job
        except LeaseLostError:
            self.logger.warning("Stopped work after its durable lease was lost.")
            return lease.job
        except Exception as error:
            return self._record_failure(lease, classify_job_error(error))

    def run_forever(self, *, install_signal_handlers: bool = True) -> None:
        """Run until SIGTERM, SIGINT, or ``request_stop``; finish active work first."""

        context = stop_on_signals(self.stop_event) if install_signal_handlers else _null_context()
        with context:
            self.recover_stale_jobs()
            while not self.stop_event.is_set():
                processed = self.run_once()
                if processed is None:
                    self.stop_event.wait(self.poll_seconds)

    def _dispatch(self, lease: JobLease) -> None:
        if lease.job.kind in {JobKind.INGEST, JobKind.REPROCESS}:
            self.ingestion_executor.process(lease)
            return
        if lease.job.kind is JobKind.DELETE:
            self.deletion_executor.execute(lease)
            return
        raise JobExecutionError(
            "unsupported_job_kind",
            "The worker received an unsupported job type.",
            retryable=False,
        )

    def _record_failure(self, lease: JobLease, error: JobExecutionError) -> IngestionJob:
        retryable = error.retryable and lease.job.attempt_count < self.max_attempts
        normalized = JobExecutionError(
            error.code,
            error.public_message,
            retryable=retryable,
        )
        delay = min(60.0, float(2 ** max(0, lease.job.attempt_count - 1))) if retryable else 0
        try:
            failed = lease.fail_error(normalized, retry_delay_seconds=delay)
        except LeaseLostError:
            self.logger.warning("Could not record failure after the durable lease was lost.")
            return lease.job
        self.logger.warning("Job ended with safe error code %s.", normalized.code)
        return failed


@contextmanager
def _null_context() -> Iterator[None]:
    yield
