"""Fail when public source contains private runtime artifacts or obvious secret assignments."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {
    ".git",
    ".venv",
    ".data",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "tmp",
}
FORBIDDEN_NAMES = {".env", ".env.local", ".env.prod", ".env.production"}
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".mdc",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SECRET_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:OPENAI_API_KEY|DOCINTEL_OPENAI_API_KEY|DOCINTEL_API_TOKEN)\s*=\s*[^\s#][^\r\n]*$"
)
MARKDOWN_TARGET = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
PLACEHOLDER_MARKERS = (
    "your-",
    "test-",
    "change-before-sharing",
    "example",
    "not-valid",
    "${",
)
REQUIRED_PUBLIC_PATHS = (
    "README.md",
    "LICENSE",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "SUPPORT.md",
    "CHANGELOG.md",
    "docs/quickstart.md",
    "docs/extending.md",
    "docs/assets/document-intelligence-workspace.png",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
)
README_MARKERS = (
    "## Try it in 5 minutes",
    "docker compose up --build -d",
    "docs/quickstart.md",
    "docs/extending.md",
    "docs/assets/document-intelligence-workspace.png",
)
PUBLIC_MARKDOWN_PATHS = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "SUPPORT.md",
    "docs/quickstart.md",
    "docs/extending.md",
)


def main() -> int:
    findings: list[str] = []
    for relative_path in REQUIRED_PUBLIC_PATHS:
        if not (ROOT / relative_path).is_file():
            findings.append(f"missing public project surface: {relative_path}")
    readme_path = ROOT / "README.md"
    if readme_path.is_file():
        readme = readme_path.read_text(encoding="utf-8")
        for marker in README_MARKERS:
            if marker not in readme:
                findings.append(f"README missing newcomer marker: {marker}")
    for relative_path in PUBLIC_MARKDOWN_PATHS:
        markdown_path = ROOT / relative_path
        if not markdown_path.is_file():
            continue
        markdown = markdown_path.read_text(encoding="utf-8")
        for target in MARKDOWN_TARGET.findall(markdown):
            target = target.strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_part = target.split("#", maxsplit=1)[0]
            if not path_part:
                continue
            candidate = (markdown_path.parent / path_part).resolve()
            if not candidate.is_relative_to(ROOT) or not candidate.exists():
                findings.append(f"broken local Markdown link: {relative_path} -> {target}")
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in SKIP_PARTS for part in relative.parts) or not path.is_file():
            continue
        if path.name in FORBIDDEN_NAMES:
            findings.append(f"private environment file: {relative}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 2_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in SECRET_ASSIGNMENT.finditer(content):
            assignment = match.group(0).casefold()
            if not any(marker in assignment for marker in PLACEHOLDER_MARKERS):
                findings.append(f"credential-like assignment: {relative}")
                break
    if findings:
        for finding in findings:
            print(f"public-tree check failed: {finding}")
        return 1
    print("public-tree check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
