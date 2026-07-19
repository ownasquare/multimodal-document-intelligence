from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from typer.testing import CliRunner

from document_intelligence import cli
from document_intelligence.config import Settings
from document_intelligence.worker import WorkerFactoryPort


class FakeWorker:
    def __init__(self) -> None:
        self.calls = 0

    def run_forever(self) -> None:
        self.calls += 1


class FakeProcess:
    def __init__(self) -> None:
        self.return_code: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.return_code = self.return_code or 0
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 0

    def kill(self) -> None:
        self.killed = True
        self.return_code = -9


def test_cli_help_exposes_complete_operator_surface() -> None:
    result = CliRunner().invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    for command in ("doctor", "sample", "api", "worker", "ui", "demo"):
        assert command in result.stdout


def test_sample_command_reports_stable_path_and_metadata() -> None:
    result = CliRunner().invoke(cli.app, ["sample", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["page_count"] == 8
    assert payload["byte_size"] > 100_000
    assert len(payload["sha256"]) == 64
    assert payload["path"].endswith("examples/northstar-q2-operations-review.pdf")


def test_doctor_never_exposes_secret_values(tmp_path: Path, monkeypatch: Any) -> None:
    settings = Settings(
        data_dir=tmp_path / "private",
        api_token="doctor-secret-value",
    )
    monkeypatch.setattr(cli, "_settings_or_exit", lambda: settings)

    result = CliRunner().invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "doctor-secret-value" not in result.stdout
    assert "provider: deterministic" in result.stdout
    assert "sample: ready" in result.stdout


def test_api_launcher_uses_public_factory_and_configured_binding(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, api_port=9123)
    captured: dict[str, object] = {}

    def launcher(app_path: str, **kwargs: object) -> None:
        captured["app_path"] = app_path
        captured.update(kwargs)

    cli.run_api(settings, launcher=launcher)

    assert captured == {
        "app_path": "document_intelligence.api:create_app",
        "host": "127.0.0.1",
        "port": 9123,
        "factory": True,
        "log_level": "info",
    }


def test_worker_factory_and_ui_command_are_injectable(tmp_path: Path) -> None:
    worker = FakeWorker()
    factory = cast(WorkerFactoryPort, lambda: worker)

    returned = cli.run_worker(factory=factory)

    assert returned is worker
    assert worker.calls == 1

    captured: list[str] = []

    def runner(argv: Sequence[str]) -> int:
        captured.extend(argv)
        return 7

    exit_code = cli.run_ui(Settings(data_dir=tmp_path, ui_port=9555), runner=runner)
    assert exit_code == 7
    assert captured[:4] == [cli.sys.executable, "-m", "streamlit", "run"]
    assert "--server.port" in captured
    assert captured[captured.index("--server.port") + 1] == "9555"


def test_demo_supervises_three_fixed_commands_and_cleans_up(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    commands: list[list[str]] = []
    processes: list[FakeProcess] = []
    loaded: list[bool] = []
    stop = threading.Event()
    stop.set()

    def process_factory(argv: Sequence[str]) -> FakeProcess:
        commands.append(list(argv))
        process = FakeProcess()
        processes.append(process)
        return process

    result = cli.run_demo(
        settings,
        process_factory=process_factory,
        api_waiter=lambda active_settings, active_processes: None,
        sample_loader=lambda active_settings: loaded.append(True),
        stop_event=stop,
    )

    assert result == 0
    assert [command[-1] for command in commands] == ["api", "worker", "ui"]
    assert loaded == [True]
    assert all(process.terminated for process in processes)


def test_demo_sample_posts_to_versioned_authenticated_route(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, api_token="test-api-token")
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str) -> object:
            captured["url"] = url
            return type("Response", (), {"status_code": 202})()

    cli.load_demo_sample(settings, client_factory=FakeClient)

    assert captured["url"] == "http://127.0.0.1:8014/api/v1/demo/sample"
    assert captured["headers"] == {
        "Authorization": "Bearer test-api-token",
        "Idempotency-Key": "demo-sample-v1",
    }
