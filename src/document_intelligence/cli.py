"""Operator CLI for diagnostics and local process orchestration."""

# ruff: noqa: S603

from __future__ import annotations

import importlib
import json
import subprocess  # nosec B404
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, cast

import httpx
import typer

from document_intelligence.config import Settings, get_settings
from document_intelligence.parsers import PDFParser, PDFParserOptions
from document_intelligence.parsers.base import sha256_file
from document_intelligence.parsers.ocr import OCRProcessor
from document_intelligence.sample import SAMPLE_PATH
from document_intelligence.worker import WorkerFactoryPort, WorkerRunner, stop_on_signals

app = typer.Typer(
    name="document-intelligence",
    help="Run and inspect the local Document Intelligence workspace.",
    no_args_is_help=True,
)

UI_APP_PATH = Path(__file__).resolve().parent / "ui" / "app.py"


class ServerLauncherPort(Protocol):
    def __call__(
        self,
        app_path: str,
        *,
        host: str,
        port: int,
        factory: bool,
        log_level: str,
    ) -> object: ...


class CommandRunnerPort(Protocol):
    def __call__(self, argv: Sequence[str]) -> int: ...


class ManagedProcessPort(Protocol):
    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class ProcessFactoryPort(Protocol):
    def __call__(self, argv: Sequence[str]) -> ManagedProcessPort: ...


class DemoStartupError(RuntimeError):
    """A sanitized local-demo startup failure."""


@dataclass(frozen=True, slots=True)
class SampleInfo:
    path: str
    page_count: int
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    healthy: bool
    provider_mode: str
    api_binding: str
    data_directory_ready: bool
    sample_ready: bool
    sample_pages: int | None
    ocr_available: bool
    warnings: tuple[str, ...]


def inspect_sample(path: Path = SAMPLE_PATH) -> SampleInfo:
    resolved = path.resolve()
    info = PDFParser(PDFParserOptions(enable_ocr=False)).validate(resolved)
    return SampleInfo(
        path=str(resolved),
        page_count=info.page_count,
        byte_size=info.byte_size,
        sha256=sha256_file(resolved),
    )


def collect_doctor_report(
    settings: Settings,
    *,
    sample_path: Path = SAMPLE_PATH,
    ocr_processor: OCRProcessor | None = None,
) -> DoctorReport:
    warnings: list[str] = []
    try:
        settings.ensure_directories()
        data_ready = all(
            path.is_dir()
            for path in (
                settings.data_dir,
                settings.uploads_dir,
                settings.artifacts_dir,
                settings.chroma_dir,
            )
        )
    except OSError:
        data_ready = False
        warnings.append("The private data directory could not be prepared.")
    sample_pages: int | None = None
    try:
        sample_pages = inspect_sample(sample_path).page_count
        sample_ready = sample_pages == 8
    except (OSError, RuntimeError):
        sample_ready = False
        warnings.append("The bundled synthetic sample is unavailable or invalid.")
    ocr = ocr_processor or OCRProcessor(enabled=settings.enable_ocr)
    if settings.enable_ocr and not ocr.available:
        warnings.append("Optional OCR is unavailable; born-digital documents remain usable.")
    api_binding = "loopback" if settings.is_loopback else "token-protected"
    return DoctorReport(
        healthy=data_ready and sample_ready,
        provider_mode=settings.provider_mode,
        api_binding=api_binding,
        data_directory_ready=data_ready,
        sample_ready=sample_ready,
        sample_pages=sample_pages,
        ocr_available=ocr.available,
        warnings=tuple(warnings),
    )


def run_api(settings: Settings, *, launcher: ServerLauncherPort | None = None) -> None:
    if launcher is None:
        uvicorn = importlib.import_module("uvicorn")
        launcher = cast(ServerLauncherPort, uvicorn.run)
    launcher(
        "document_intelligence.api:create_app",
        host=settings.api_host,
        port=settings.api_port,
        factory=True,
        log_level="info",
    )


def load_worker_factory() -> WorkerFactoryPort:
    container_module = importlib.import_module("document_intelligence.container")
    factory = getattr(container_module, "create_worker", None)
    if not callable(factory):
        raise RuntimeError("Worker runtime factory is unavailable.")
    return cast(WorkerFactoryPort, factory)


def run_worker(*, factory: WorkerFactoryPort | None = None) -> WorkerRunner:
    worker = (factory or load_worker_factory())()
    worker.run_forever()
    return worker


def _run_command(argv: Sequence[str]) -> int:
    # Callers provide only this module's fixed executable argv; shell parsing stays disabled.
    completed = subprocess.run(list(argv), check=False, shell=False)  # nosec
    return completed.returncode


def run_ui(settings: Settings, *, runner: CommandRunnerPort = _run_command) -> int:
    argv = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(UI_APP_PATH),
        "--server.address",
        settings.api_host,
        "--server.port",
        str(settings.ui_port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    return runner(argv)


def _spawn_process(argv: Sequence[str]) -> ManagedProcessPort:
    # Demo service names are mapped to fixed module argv by _service_command.
    return subprocess.Popen(list(argv), shell=False)  # nosec


def _service_command(name: str) -> list[str]:
    return [sys.executable, "-m", "document_intelligence.cli", name]


def _auth_headers(settings: Settings) -> dict[str, str]:
    if settings.api_token is None:
        return {}
    return {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}


def wait_for_api(
    settings: Settings,
    processes: Sequence[ManagedProcessPort],
    *,
    timeout_seconds: float = 45.0,
    poll_seconds: float = 0.25,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    ready_url = settings.resolved_api_base_url.rstrip("/") + "/health/ready"
    with client_factory(timeout=2.0, headers=_auth_headers(settings)) as client:
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                raise DemoStartupError("The API process exited before becoming ready.")
            try:
                response = client.get(ready_url)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(poll_seconds)
    raise DemoStartupError("The API did not become ready before the startup deadline.")


def load_demo_sample(
    settings: Settings,
    *,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> None:
    sample_url = settings.resolved_api_base_url.rstrip("/") + "/api/v1/demo/sample"
    headers = _auth_headers(settings)
    headers["Idempotency-Key"] = "demo-sample-v1"
    try:
        with client_factory(timeout=30.0, headers=headers) as client:
            response = client.post(sample_url)
    except httpx.HTTPError as error:
        raise DemoStartupError("The synthetic sample request could not reach the API.") from error
    if response.status_code not in {200, 201, 202, 409}:
        raise DemoStartupError("The API did not accept the synthetic sample request.")


def _stop_processes(processes: Sequence[ManagedProcessPort]) -> None:
    running = [process for process in processes if process.poll() is None]
    for process in running:
        process.terminate()
    for process in running:
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)


def run_demo(
    settings: Settings,
    *,
    process_factory: ProcessFactoryPort = _spawn_process,
    api_waiter: Callable[[Settings, Sequence[ManagedProcessPort]], None] = wait_for_api,
    sample_loader: Callable[[Settings], None] = load_demo_sample,
    stop_event: threading.Event | None = None,
    poll_seconds: float = 0.25,
) -> int:
    """Supervise API, worker, and UI and load the sample through the API."""

    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    stop = stop_event or threading.Event()
    processes: list[ManagedProcessPort] = []
    try:
        api_process = process_factory(_service_command("api"))
        processes.append(api_process)
        api_waiter(settings, processes)
        processes.extend(
            [
                process_factory(_service_command("worker")),
                process_factory(_service_command("ui")),
            ]
        )
        sample_loader(settings)
        with stop_on_signals(stop):
            while not stop.wait(poll_seconds):
                exit_codes = [process.poll() for process in processes]
                if any(code is not None for code in exit_codes):
                    return next(
                        (code for code in exit_codes if code is not None and code != 0),
                        1,
                    )
        return 0
    finally:
        _stop_processes(processes)


def _settings_or_exit() -> Settings:
    try:
        return get_settings()
    except Exception as error:
        raise typer.BadParameter("Runtime configuration is invalid.") from error


@app.command()
def doctor() -> None:
    """Check local configuration without making provider calls."""

    report = collect_doctor_report(_settings_or_exit())
    typer.echo(f"status: {'ready' if report.healthy else 'needs attention'}")
    typer.echo(f"provider: {report.provider_mode}")
    typer.echo(f"api binding: {report.api_binding}")
    typer.echo(f"data directory: {'ready' if report.data_directory_ready else 'unavailable'}")
    typer.echo(f"sample: {'ready' if report.sample_ready else 'unavailable'}")
    typer.echo(f"ocr: {'ready' if report.ocr_available else 'optional/unavailable'}")
    for warning in report.warnings:
        typer.echo(f"warning: {warning}")
    if not report.healthy:
        raise typer.Exit(1)


@app.command()
def sample(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Print the bundled synthetic sample path and immutable metadata."""

    try:
        info = inspect_sample()
    except (OSError, RuntimeError) as error:
        raise typer.BadParameter("The bundled synthetic sample is unavailable.") from error
    if json_output:
        typer.echo(json.dumps(asdict(info), sort_keys=True))
        return
    typer.echo(f"path: {info.path}")
    typer.echo(f"pages: {info.page_count}")
    typer.echo(f"bytes: {info.byte_size}")
    typer.echo(f"sha256: {info.sha256}")


@app.command("api")
def api_command() -> None:
    """Start the FastAPI system-of-record service."""

    run_api(_settings_or_exit())


@app.command("worker")
def worker_command() -> None:
    """Start the durable single-job worker loop."""

    try:
        run_worker()
    except (ImportError, AttributeError, RuntimeError) as error:
        raise typer.BadParameter("The worker runtime is unavailable.") from error


@app.command("ui")
def ui_command() -> None:
    """Start the Streamlit workspace."""

    raise typer.Exit(run_ui(_settings_or_exit()))


@app.command("demo")
def demo_command() -> None:
    """Start the credential-free local topology and load the sample."""

    settings = _settings_or_exit()
    if settings.provider_mode != "deterministic" or settings.embedding_provider != "deterministic":
        raise typer.BadParameter("Demo requires deterministic provider and embedding modes.")
    try:
        exit_code = run_demo(settings)
    except DemoStartupError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    raise typer.Exit(exit_code)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
