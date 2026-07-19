"""Structured table extraction with row-level provenance."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from document_intelligence.parsers.base import SourceBBox


def normalize_cell(value: object | None) -> str:
    """Normalize extractor whitespace while preserving visible cell content."""

    if value is None:
        return ""
    return " ".join(str(value).replace("\u0000", "").split())


def normalize_headers(values: Sequence[object | None]) -> tuple[str, ...]:
    """Create stable, unique column names for incomplete extractor output."""

    headers: list[str] = []
    counts: dict[str, int] = {}
    for index, raw_value in enumerate(values):
        base = normalize_cell(raw_value) or f"column_{index + 1}"
        count = counts.get(base, 0) + 1
        counts[base] = count
        headers.append(base if count == 1 else f"{base}_{count}")
    return tuple(headers)


def _unit_from_header(header: str) -> str | None:
    match = re.search(r"\(([^)]+)\)", header)
    return match.group(1).strip() if match else None


@dataclass(frozen=True, slots=True)
class ExtractedTable:
    page_number: int
    bbox: SourceBBox
    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    caption: str | None = None
    footnotes: tuple[str, ...] = ()

    @property
    def units(self) -> dict[str, str]:
        return {
            header: unit
            for header in self.headers
            if (unit := _unit_from_header(header)) is not None
        }

    def summary_text(self) -> str:
        column_text = " | ".join(self.headers)
        parts = [f"Table with columns: {column_text}. {len(self.rows)} data rows."]
        if self.caption:
            parts.append(f"Caption: {self.caption}")
        if self.footnotes:
            parts.append("Footnote: " + " ".join(self.footnotes))
        return " ".join(parts)

    def row_text(self, row_index: int) -> str:
        row = self.rows[row_index]
        return " | ".join(f"{header}: {row.get(header, '')}" for header in self.headers)


def structure_table(
    raw_rows: list[list[object | None]],
    *,
    page_number: int,
    bbox: SourceBBox,
    caption: str | None = None,
    footnotes: tuple[str, ...] = (),
) -> ExtractedTable | None:
    """Convert pdfplumber rows into a header plus stable row dictionaries."""

    cleaned = [[normalize_cell(cell) for cell in row] for row in raw_rows]
    cleaned = [row for row in cleaned if any(row)]
    if len(cleaned) < 2:
        return None
    width = max(len(row) for row in cleaned)
    padded = [row + [""] * (width - len(row)) for row in cleaned]
    headers = normalize_headers(padded[0])
    records = tuple(
        {header: row[index] for index, header in enumerate(headers)}
        for row in padded[1:]
        if any(row)
    )
    if not records:
        return None
    return ExtractedTable(
        page_number=page_number,
        bbox=bbox,
        headers=headers,
        rows=records,
        caption=caption or None,
        footnotes=tuple(note for note in footnotes if note),
    )


def _nearby_text(page: Any, bbox: SourceBBox, *, above: bool) -> str | None:
    x0, top, x1, bottom = bbox
    if above:
        crop_bbox = (x0, max(0.0, top - 42.0), x1, top)
    else:
        crop_bbox = (x0, bottom, x1, min(float(page.height), bottom + 62.0))
    if crop_bbox[3] <= crop_bbox[1]:
        return None
    try:
        value = page.crop(crop_bbox, strict=False).extract_text() or ""
    except (ValueError, TypeError):
        return None
    normalized = normalize_cell(value)
    return normalized or None


def extract_tables(page: Any, *, page_number: int) -> list[ExtractedTable]:
    """Extract all usable tables from a pdfplumber page."""

    extracted: list[ExtractedTable] = []
    try:
        candidates = page.find_tables()
    except (AttributeError, TypeError, ValueError):
        return extracted
    for candidate in candidates:
        raw_bbox = tuple(float(value) for value in candidate.bbox)
        if len(raw_bbox) != 4:
            continue
        rows = candidate.extract() or []
        table = structure_table(
            rows,
            page_number=page_number,
            bbox=(raw_bbox[0], raw_bbox[1], raw_bbox[2], raw_bbox[3]),
            caption=_nearby_text(page, raw_bbox, above=True),
            footnotes=tuple(
                note for note in (_nearby_text(page, raw_bbox, above=False),) if note is not None
            ),
        )
        if table is not None:
            extracted.append(table)
    return extracted
