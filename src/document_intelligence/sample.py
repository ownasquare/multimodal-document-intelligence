"""Resolve the bundled synthetic PDF in source and installed distributions."""

from __future__ import annotations

from pathlib import Path

SAMPLE_FILENAME = "northstar-q2-operations-review.pdf"


def resolve_bundled_sample_path(*, module_file: Path | None = None) -> Path:
    """Return the checked-in source sample or its installed package-data fallback."""

    location = (module_file or Path(__file__)).resolve()
    package_directory = location.parent
    source_sample = package_directory.parents[1] / "examples" / SAMPLE_FILENAME
    if source_sample.is_file():
        return source_sample
    return package_directory / "resources" / SAMPLE_FILENAME


SAMPLE_PATH = resolve_bundled_sample_path()
