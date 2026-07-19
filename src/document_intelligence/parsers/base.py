"""Shared parser contracts and safety helpers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

NormalizedBBox = tuple[float, float, float, float]
SourceBBox = tuple[float, float, float, float]


class ElementModality(StrEnum):
    """Parser-level modalities aligned with the durable domain model."""

    TEXT = "text"
    TABLE = "table"
    TABLE_ROW = "table_row"
    IMAGE = "image"
    CHART = "chart"
    DIAGRAM = "diagram"
    OCR = "ocr"


class ParserError(Exception):
    """Base error with a stable, non-sensitive machine code."""

    code = "parser_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidPDFError(ParserError):
    code = "invalid_pdf"


class EncryptedPDFError(ParserError):
    code = "encrypted_pdf"


class CorruptPDFError(ParserError):
    code = "corrupt_pdf"


class PDFLimitError(ParserError):
    code = "pdf_limit_exceeded"


@dataclass(frozen=True, slots=True)
class ParseWarning:
    """A recoverable page or document parsing issue."""

    code: str
    message: str
    page_number: int | None = None


@dataclass(slots=True)
class ParsedElement:
    """One observed or derived piece of page content."""

    page_number: int
    modality: ElementModality
    content: str
    extraction_method: str
    bbox: NormalizedBBox | None = None
    confidence: float = 1.0
    asset_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("page_number must be at least 1")
        if not self.content.strip():
            raise ValueError("content must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        self.metadata.setdefault("content_trust", "untrusted")


@dataclass(slots=True)
class ParsedPage:
    """Parsed content and derived assets for one page."""

    page_number: int
    width: float
    height: float
    native_text: str
    elements: list[ParsedElement]
    page_asset_path: Path | None
    warnings: list[ParseWarning] = field(default_factory=list)


@dataclass(slots=True)
class ParsedDocument:
    """Bounded parse result for an immutable source PDF."""

    source_path: Path
    sha256: str
    pages: list[ParsedPage]
    warnings: list[ParseWarning] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def elements(self) -> list[ParsedElement]:
        return [element for page in self.pages for element in page.elements]


@dataclass(frozen=True, slots=True)
class PDFInfo:
    page_count: int
    byte_size: int
    encrypted: bool = False


@dataclass(frozen=True, slots=True)
class PDFParserOptions:
    max_file_bytes: int = 50 * 1024 * 1024
    max_pages: int = 250
    render_scale: float = 1.5
    enable_ocr: bool = True
    ocr_timeout_seconds: int = 30
    minimum_native_characters: int = 40
    minimum_visual_area_ratio: float = 0.01

    def __post_init__(self) -> None:
        if self.max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        if self.max_pages < 1:
            raise ValueError("max_pages must be positive")
        if not 0.5 <= self.render_scale <= 4.0:
            raise ValueError("render_scale must be between 0.5 and 4.0")
        if self.ocr_timeout_seconds < 1:
            raise ValueError("ocr_timeout_seconds must be positive")
        if self.minimum_native_characters < 0:
            raise ValueError("minimum_native_characters must not be negative")


def normalize_bbox(bbox: SourceBBox, *, page_width: float, page_height: float) -> NormalizedBBox:
    """Normalize a pdfplumber top-origin box to values in the closed unit square."""

    if page_width <= 0 or page_height <= 0:
        raise ValueError("page dimensions must be positive")
    x0, top, x1, bottom = bbox
    left = min(max(x0 / page_width, 0.0), 1.0)
    upper = min(max(top / page_height, 0.0), 1.0)
    right = min(max(x1 / page_width, 0.0), 1.0)
    lower = min(max(bottom / page_height, 0.0), 1.0)
    if right < left or lower < upper:
        raise ValueError("bbox coordinates are inverted")
    return (round(left, 6), round(upper, 6), round(right, 6), round(lower, 6))


def bbox_overlap_ratio(inner: SourceBBox, outer: SourceBBox) -> float:
    """Return how much of *inner* is covered by *outer*."""

    ix0, itop, ix1, ibottom = inner
    ox0, otop, ox1, obottom = outer
    width = max(0.0, min(ix1, ox1) - max(ix0, ox0))
    height = max(0.0, min(ibottom, obottom) - max(itop, otop))
    intersection = width * height
    inner_area = max(0.0, ix1 - ix0) * max(0.0, ibottom - itop)
    return intersection / inner_area if inner_area else 0.0


def canonical_repeated_text(value: str) -> str:
    """Normalize changing page numbers so repeated furniture can be detected."""

    collapsed = re.sub(r"\d+", "#", value.casefold())
    return " ".join(collapsed.split())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
