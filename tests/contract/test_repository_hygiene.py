"""Public-release and local deployment contract tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_public_tree_checker_passes() -> None:
    result = subprocess.run(  # noqa: S603 - fixed interpreter and repository script
        [sys.executable, str(ROOT / "scripts" / "check_public_repo.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "public-tree check passed" in result.stdout


def test_compose_is_loopback_non_root_and_capability_dropped() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert '"127.0.0.1:8014:8014"' in compose
    assert '"127.0.0.1:8514:8514"' in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    assert "read_only: true" in compose
    assert "USER app" in dockerfile
    assert "tesseract-ocr" in dockerfile
    data_directory = dockerfile.index("mkdir -p /data")
    data_ownership = dockerfile.index("chown app:app /data")
    non_root_runtime = dockerfile.index("USER app")
    assert data_directory < data_ownership < non_root_runtime
    assert dockerfile.index("COPY examples ./examples") < dockerfile.index("uv sync --frozen")


def test_newcomer_docs_and_github_surfaces_are_discoverable() -> None:
    required_paths = (
        "CODE_OF_CONDUCT.md",
        "docs/quickstart.md",
        "docs/assets/document-intelligence-workspace.png",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/pull_request_template.md",
    )
    for relative_path in required_paths:
        assert (ROOT / relative_path).is_file(), relative_path

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for expected in (
        "## Try it in 5 minutes",
        "docker compose up --build -d",
        "docs/quickstart.md",
        "docs/extending.md",
        "docs/assets/document-intelligence-workspace.png",
        "No API key is needed",
    ):
        assert expected in readme


def test_ci_uses_current_node24_action_majors() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/checkout@v7" in workflow
    assert "astral-sh/setup-uv@v8.3.2" in workflow
    assert "docker/setup-buildx-action@v4" in workflow
    assert "actions/upload-artifact@v7.0.1" in workflow
    for outdated in ("actions/checkout@v4", "astral-sh/setup-uv@v6", "setup-buildx-action@v3"):
        assert outdated not in workflow


def test_rendered_e2e_is_isolated_deterministic_and_failure_diagnostic() -> None:
    compose_path = ROOT / "compose.e2e.yaml"
    assert compose_path.is_file()
    compose = compose_path.read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for expected in (
        "name: document-intelligence-e2e",
        "image: document-intelligence:e2e",
        "127.0.0.1:${DOCINTEL_E2E_API_PORT:-18014}:8014",
        "127.0.0.1:${DOCINTEL_E2E_UI_PORT:-18514}:8514",
        "DOCINTEL_PROVIDER_MODE: deterministic",
        "DOCINTEL_EMBEDDING_PROVIDER: deterministic",
        'DOCINTEL_OPENAI_API_KEY: ""',
        "ports: !override",
    ):
        assert expected in compose

    for expected in (
        "rendered-e2e:",
        "uv run playwright install --with-deps chromium",
        "--project-name document-intelligence-e2e",
        "-f compose.e2e.yaml",
        "--wait-timeout 300",
        "-m e2e",
        "--tracing retain-on-failure",
        "--screenshot only-on-failure",
        "if: failure()",
        "actions/upload-artifact@v7.0.1",
        "down --timeout 30 --volumes --remove-orphans",
        "if: always()",
    ):
        assert expected in workflow

    assert "run: >-" in workflow
    assert "test_document_workspace.py +" not in workflow
    assert makefile.count('-m "not live and not e2e"') >= 2
