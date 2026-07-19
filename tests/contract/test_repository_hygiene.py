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
