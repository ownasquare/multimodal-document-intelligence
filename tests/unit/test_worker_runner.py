from __future__ import annotations

import signal
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from document_intelligence.jobs import JobExecutionError
from document_intelligence.models import IngestionJob, JobKind, JobStage, JobStatus
from document_intelligence.worker import WorkerRunner, stop_on_signals


def make_job(kind: JobKind, *, attempt_count: int = 1) -> IngestionJob:
    now = datetime.now(UTC)
    return IngestionJob(
        id=f"job-{kind.value}-{attempt_count}",
        workspace_id="workspace-1",
        document_id="document-1",
        version_id="version-1",
        kind=kind,
        status=JobStatus.RUNNING,
        stage=JobStage.QUEUED,
        attempt_count=attempt_count,
        lease_owner="worker-1",
        lease_expires_at=now + timedelta(seconds=30),
    )


class FakeLease:
    def __init__(
        self,
        job: IngestionJob,
        *,
        cancelled: bool = False,
        heartbeat_error: Exception | None = None,
    ) -> None:
        self.job = job
        self._cancelled = cancelled
        self.heartbeat_error = heartbeat_error
        self.heartbeat_count = 0
        self.failure: tuple[str, bool, float] | None = None

    @property
    def cancellation_requested(self) -> bool:
        return self._cancelled

    def heartbeat(self) -> FakeLease:
        self.heartbeat_count += 1
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        return self

    def acknowledge_cancellation(self) -> IngestionJob:
        self.job = self.job.model_copy(update={"status": JobStatus.CANCELLED})
        return self.job

    def complete(self) -> IngestionJob:
        self.job = self.job.model_copy(
            update={"status": JobStatus.SUCCEEDED, "stage": JobStage.COMPLETE, "progress": 1.0}
        )
        return self.job

    def fail_error(
        self,
        error: JobExecutionError,
        *,
        retry_delay_seconds: float = 0,
    ) -> IngestionJob:
        self.failure = (error.code, error.retryable, retry_delay_seconds)
        status = JobStatus.QUEUED if error.retryable else JobStatus.FAILED
        self.job = self.job.model_copy(
            update={
                "status": status,
                "error_code": error.code,
                "error_message": error.public_message,
            }
        )
        return self.job


class FakeCoordinator:
    def __init__(self, leases: list[FakeLease], *, recovered: int = 0) -> None:
        self.leases = leases
        self.lease_seconds = 1.0
        self.recovered = recovered
        self.recover_calls = 0

    def recover(self) -> int:
        self.recover_calls += 1
        return self.recovered

    def lease(self) -> FakeLease | None:
        return self.leases.pop(0) if self.leases else None


class RecordingExecutor:
    def __init__(self, action: Callable[[FakeLease], None] | None = None) -> None:
        self.calls: list[FakeLease] = []
        self.action = action or (lambda lease: lease.complete())

    def execute(self, lease: FakeLease) -> object:
        self.calls.append(lease)
        self.action(lease)
        return lease.job

    def process(self, lease: FakeLease) -> object:
        return self.execute(lease)


def make_runner(
    coordinator: FakeCoordinator,
    ingestion: RecordingExecutor,
    deletion: RecordingExecutor,
    **kwargs: Any,
) -> WorkerRunner:
    return WorkerRunner(
        coordinator,  # type: ignore[arg-type]
        ingestion_executor=ingestion,  # type: ignore[arg-type]
        deletion_executor=deletion,  # type: ignore[arg-type]
        heartbeat_interval_seconds=kwargs.pop("heartbeat_interval_seconds", 0.05),
        **kwargs,
    )


def test_runner_dispatches_ingest_reprocess_and_delete_to_exact_executor() -> None:
    leases = [
        FakeLease(make_job(JobKind.INGEST)),
        FakeLease(make_job(JobKind.REPROCESS)),
        FakeLease(make_job(JobKind.DELETE)),
    ]
    coordinator = FakeCoordinator(leases.copy())
    ingestion = RecordingExecutor()
    deletion = RecordingExecutor()
    runner = make_runner(coordinator, ingestion, deletion)

    assert runner.run_once().status is JobStatus.SUCCEEDED  # type: ignore[union-attr]
    assert runner.run_once().status is JobStatus.SUCCEEDED  # type: ignore[union-attr]
    assert runner.run_once().status is JobStatus.SUCCEEDED  # type: ignore[union-attr]
    assert [lease.job.kind for lease in ingestion.calls] == [JobKind.INGEST, JobKind.REPROCESS]
    assert [lease.job.kind for lease in deletion.calls] == [JobKind.DELETE]


def test_background_heartbeat_covers_synchronous_executor_work() -> None:
    lease = FakeLease(make_job(JobKind.INGEST))

    def slow_complete(active_lease: FakeLease) -> None:
        time.sleep(0.07)
        active_lease.complete()

    runner = make_runner(
        FakeCoordinator([lease]),
        RecordingExecutor(slow_complete),
        RecordingExecutor(),
        heartbeat_interval_seconds=0.01,
    )

    result = runner.run_once()

    assert result is not None and result.status is JobStatus.SUCCEEDED
    assert lease.heartbeat_count >= 2


def test_cancellation_is_acknowledged_before_dispatch() -> None:
    lease = FakeLease(make_job(JobKind.INGEST), cancelled=True)
    ingestion = RecordingExecutor()
    result = make_runner(FakeCoordinator([lease]), ingestion, RecordingExecutor()).run_once()

    assert result is not None and result.status is JobStatus.CANCELLED
    assert ingestion.calls == []


@pytest.mark.parametrize(
    ("attempt_count", "expected_status", "expected_retryable"),
    [(1, JobStatus.QUEUED, True), (3, JobStatus.FAILED, False)],
)
def test_retryable_failure_is_bounded_by_attempt_limit(
    attempt_count: int,
    expected_status: JobStatus,
    expected_retryable: bool,
) -> None:
    lease = FakeLease(make_job(JobKind.INGEST, attempt_count=attempt_count))

    def time_out(active_lease: FakeLease) -> None:
        del active_lease
        raise TimeoutError

    runner = make_runner(
        FakeCoordinator([lease]),
        RecordingExecutor(time_out),
        RecordingExecutor(),
        max_attempts=3,
    )
    result = runner.run_once()

    assert result is not None and result.status is expected_status
    assert lease.failure is not None
    assert lease.failure[0] == "operation_timeout"
    assert lease.failure[1] is expected_retryable
    assert "TimeoutError" not in (result.error_message or "")


def test_executor_must_finalize_successful_lease() -> None:
    lease = FakeLease(make_job(JobKind.INGEST))
    runner = make_runner(
        FakeCoordinator([lease]),
        RecordingExecutor(lambda active_lease: None),
        RecordingExecutor(),
    )

    result = runner.run_once()

    assert result is not None and result.status is JobStatus.FAILED
    assert lease.failure == ("executor_incomplete", False, 0)


def test_runner_does_not_swallow_keyboard_interrupt() -> None:
    lease = FakeLease(make_job(JobKind.INGEST))

    def interrupt(active_lease: FakeLease) -> None:
        del active_lease
        raise KeyboardInterrupt

    runner = make_runner(
        FakeCoordinator([lease]),
        RecordingExecutor(interrupt),
        RecordingExecutor(),
    )

    with pytest.raises(KeyboardInterrupt):
        runner.run_once()


def test_run_forever_recovers_stale_jobs_before_observing_stop() -> None:
    stop = threading.Event()
    stop.set()
    coordinator = FakeCoordinator([], recovered=2)
    runner = make_runner(
        coordinator,
        RecordingExecutor(),
        RecordingExecutor(),
        stop_event=stop,
    )

    runner.run_forever(install_signal_handlers=False)

    assert coordinator.recover_calls == 1


def test_signal_context_requests_stop_and_restores_handler() -> None:
    stop = threading.Event()
    previous = signal.getsignal(signal.SIGTERM)

    with stop_on_signals(stop):
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        assert stop.is_set()

    assert signal.getsignal(signal.SIGTERM) == previous
