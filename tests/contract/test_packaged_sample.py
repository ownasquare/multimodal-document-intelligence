from __future__ import annotations

import tomllib
from pathlib import Path

from document_intelligence import cli, container
from document_intelligence.sample import SAMPLE_FILENAME, resolve_bundled_sample_path

ROOT = Path(__file__).resolve().parents[2]


def test_cli_and_container_share_the_source_sample_path() -> None:
    resolved = resolve_bundled_sample_path()

    assert resolved == ROOT / "examples" / SAMPLE_FILENAME
    assert resolved.is_file()
    assert resolved == cli.SAMPLE_PATH
    assert resolved == container.SAMPLE_PATH


def test_sample_resolver_falls_back_to_installed_package_data(tmp_path: Path) -> None:
    module_file = (
        tmp_path
        / "venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "document_intelligence"
        / "sample.py"
    )
    packaged_sample = module_file.parent / "resources" / SAMPLE_FILENAME
    packaged_sample.parent.mkdir(parents=True)
    packaged_sample.write_bytes(b"packaged-pdf")

    assert resolve_bundled_sample_path(module_file=module_file) == packaged_sample


def test_wheel_force_includes_the_bundled_sample() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    force_include = project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert force_include[f"examples/{SAMPLE_FILENAME}"] == (
        f"document_intelligence/resources/{SAMPLE_FILENAME}"
    )
