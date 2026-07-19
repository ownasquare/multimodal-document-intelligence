"""Bounded multimodal PDF parser using pdfplumber and pypdfium2."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pdfplumber
import pypdfium2 as pdfium
from pdfminer.pdfdocument import PDFPasswordIncorrect
from PIL import Image

from document_intelligence.parsers.base import (
    CorruptPDFError,
    ElementModality,
    EncryptedPDFError,
    InvalidPDFError,
    ParsedDocument,
    ParsedElement,
    ParsedPage,
    ParseWarning,
    PDFInfo,
    PDFLimitError,
    PDFParserOptions,
    SourceBBox,
    bbox_overlap_ratio,
    canonical_repeated_text,
    normalize_bbox,
    sha256_file,
)
from document_intelligence.parsers.ocr import OCRProcessor
from document_intelligence.parsers.tables import ExtractedTable, extract_tables
from document_intelligence.parsers.visuals import (
    classify_visual,
    observed_image_metadata,
    visual_content,
)


@dataclass(frozen=True, slots=True)
class _TextBlock:
    content: str
    bbox: SourceBBox


@dataclass(slots=True)
class _PageDraft:
    page_number: int
    width: float
    height: float
    native_source_text: str
    elements: list[ParsedElement]
    page_asset_path: Path | None
    warnings: list[ParseWarning]


def _line_blocks(words: list[dict[str, Any]]) -> list[_TextBlock]:
    """Group positioned words into deterministic reading-order line blocks."""

    valid = [
        word
        for word in words
        if str(word.get("text", "")).strip()
        and all(key in word for key in ("x0", "x1", "top", "bottom"))
    ]
    valid.sort(key=lambda word: (round(float(word["top"]), 1), float(word["x0"])))
    lines: list[list[dict[str, Any]]] = []
    line_tops: list[float] = []
    for word in valid:
        top = float(word["top"])
        if lines and abs(top - line_tops[-1]) <= 3.5:
            lines[-1].append(word)
            line_tops[-1] = sum(float(item["top"]) for item in lines[-1]) / len(lines[-1])
        else:
            lines.append([word])
            line_tops.append(top)
    blocks: list[_TextBlock] = []
    for line in lines:
        ordered = sorted(line, key=lambda word: float(word["x0"]))
        segments: list[list[dict[str, Any]]] = []
        for word in ordered:
            if segments and float(word["x0"]) - float(segments[-1][-1]["x1"]) <= 36.0:
                segments[-1].append(word)
            else:
                segments.append([word])
        for segment in segments:
            content = " ".join(" ".join(str(word["text"]).split()) for word in segment).strip()
            if not content:
                continue
            bbox = (
                min(float(word["x0"]) for word in segment),
                min(float(word["top"]) for word in segment),
                max(float(word["x1"]) for word in segment),
                max(float(word["bottom"]) for word in segment),
            )
            blocks.append(_TextBlock(content=content, bbox=bbox))
    return blocks


def _extract_text_blocks(page: Any, tables: list[ExtractedTable]) -> list[_TextBlock]:
    try:
        words = page.extract_words(
            x_tolerance=2,
            y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=True,
        )
    except (TypeError, ValueError):
        return []
    blocks = _line_blocks(words)
    return [
        block
        for block in blocks
        if not any(bbox_overlap_ratio(block.bbox, table.bbox) >= 0.55 for table in tables)
    ]


def _table_elements(
    table: ExtractedTable, *, page_width: float, page_height: float
) -> list[ParsedElement]:
    normalized = normalize_bbox(table.bbox, page_width=page_width, page_height=page_height)
    common_metadata: dict[str, Any] = {
        "headers": list(table.headers),
        "units": table.units,
        "caption": table.caption,
        "footnotes": list(table.footnotes),
        "source_bbox": list(table.bbox),
        "directly_observed": True,
    }
    elements = [
        ParsedElement(
            page_number=table.page_number,
            modality=ElementModality.TABLE,
            content=table.summary_text(),
            extraction_method="pdfplumber_table",
            bbox=normalized,
            metadata={**common_metadata, "row_count": len(table.rows)},
        )
    ]
    for row_index, row in enumerate(table.rows):
        elements.append(
            ParsedElement(
                page_number=table.page_number,
                modality=ElementModality.TABLE_ROW,
                content=table.row_text(row_index),
                extraction_method="pdfplumber_table_row",
                bbox=normalized,
                metadata={
                    **common_metadata,
                    "row_index": row_index,
                    "values": row,
                    "parent_table_index": 0,
                },
            )
        )
    return elements


def _render_page(page: Any, *, scale: float) -> Image.Image:
    bitmap = page.render(scale=scale)
    try:
        pil_image = cast(Image.Image, bitmap.to_pil())
        return pil_image.convert("RGB").copy()
    finally:
        bitmap.close()


def _save_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=False, compress_level=9)


def _image_bbox(raw_image: dict[str, Any]) -> SourceBBox | None:
    try:
        x0 = float(raw_image["x0"])
        x1 = float(raw_image["x1"])
        top = float(raw_image["top"])
        bottom = float(raw_image["bottom"])
    except (KeyError, TypeError, ValueError):
        return None
    if x1 <= x0 or bottom <= top:
        return None
    return (x0, top, x1, bottom)


def _crop_render(
    rendered: Image.Image, bbox: SourceBBox, *, width: float, height: float
) -> Image.Image | None:
    x_scale = rendered.width / width
    y_scale = rendered.height / height
    left = max(0, min(rendered.width, round(bbox[0] * x_scale)))
    top = max(0, min(rendered.height, round(bbox[1] * y_scale)))
    right = max(0, min(rendered.width, round(bbox[2] * x_scale)))
    bottom = max(0, min(rendered.height, round(bbox[3] * y_scale)))
    if right - left < 2 or bottom - top < 2:
        return None
    return rendered.crop((left, top, right, bottom)).convert("RGB")


def _visual_elements(
    *,
    page: Any,
    page_number: int,
    rendered: Image.Image,
    native_text: str,
    artifact_dir: Path,
    minimum_area_ratio: float,
) -> list[ParsedElement]:
    width = float(page.width)
    height = float(page.height)
    visuals: list[ParsedElement] = []
    seen: set[tuple[float, float, float, float]] = set()
    for image_index, raw_image in enumerate(page.images, start=1):
        bbox = _image_bbox(raw_image)
        if bbox is None:
            continue
        key = (
            round(bbox[0], 2),
            round(bbox[1], 2),
            round(bbox[2], 2),
            round(bbox[3], 2),
        )
        if key in seen:
            continue
        seen.add(key)
        area_ratio = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / (width * height)
        if area_ratio < minimum_area_ratio:
            continue
        crop = _crop_render(rendered, bbox, width=width, height=height)
        if crop is None:
            continue
        asset_path = (
            artifact_dir / f"page-{page_number:03d}-image-{image_index:02d}.png"
        ).resolve()
        _save_png(crop, asset_path)
        normalized = normalize_bbox(bbox, page_width=width, page_height=height)
        classification = classify_visual(
            surrounding_text=native_text,
            bbox=normalized,
            native_character_count=len("".join(native_text.split())),
        )
        metadata = observed_image_metadata(crop)
        metadata.update(
            {
                "asset_key": asset_path.name,
                "source_bbox": list(bbox),
                "classification_method": classification.method,
                "visual_type_inferred": True,
                "area_ratio": round(area_ratio, 6),
            }
        )
        visuals.append(
            ParsedElement(
                page_number=page_number,
                modality=classification.modality,
                content=visual_content(
                    modality=classification.modality,
                    page_number=page_number,
                    surrounding_text=native_text,
                ),
                extraction_method=classification.method,
                bbox=normalized,
                confidence=classification.confidence,
                asset_path=asset_path,
                metadata=metadata,
            )
        )
    return visuals


def _suppress_repeated_furniture(drafts: list[_PageDraft]) -> None:
    pages_by_key: dict[str, set[int]] = defaultdict(set)
    for draft in drafts:
        for element in draft.elements:
            if element.modality is not ElementModality.TEXT or element.bbox is None:
                continue
            is_furniture_zone = element.bbox[1] <= 0.065 or element.bbox[3] >= 0.94
            if is_furniture_zone:
                pages_by_key[canonical_repeated_text(element.content)].add(draft.page_number)
    threshold = max(2, (len(drafts) + 1) // 2)
    repeated = {key for key, pages in pages_by_key.items() if len(pages) >= threshold}
    for draft in drafts:
        if repeated:
            draft.elements = [
                element
                for element in draft.elements
                if not (
                    element.modality is ElementModality.TEXT
                    and element.bbox is not None
                    and (element.bbox[1] <= 0.065 or element.bbox[3] >= 0.94)
                    and canonical_repeated_text(element.content) in repeated
                )
            ]
        clean_context = " ".join(
            element.content
            for element in draft.elements
            if element.modality is ElementModality.TEXT
        )
        for element in draft.elements:
            if element.modality in {
                ElementModality.IMAGE,
                ElementModality.CHART,
                ElementModality.DIAGRAM,
            }:
                element.content = visual_content(
                    modality=element.modality,
                    page_number=draft.page_number,
                    surrounding_text=clean_context,
                )


def _element_sort_key(element: ParsedElement) -> tuple[float, float, str]:
    if element.bbox is None:
        return (1.0, 1.0, element.modality.value)
    return (element.bbox[1], element.bbox[0], element.modality.value)


class PDFParser:
    """Validate, render, and parse a PDF within explicit resource bounds."""

    def __init__(
        self,
        options: PDFParserOptions | None = None,
        *,
        ocr_processor: OCRProcessor | None = None,
    ) -> None:
        self.options = options or PDFParserOptions()
        self.ocr_processor = ocr_processor or OCRProcessor(
            enabled=self.options.enable_ocr,
            timeout_seconds=self.options.ocr_timeout_seconds,
        )

    def validate(self, source_path: Path) -> PDFInfo:
        path = source_path.resolve()
        if not path.is_file():
            raise InvalidPDFError("The PDF source does not exist or is not a regular file.")
        byte_size = path.stat().st_size
        if byte_size > self.options.max_file_bytes:
            raise PDFLimitError(
                f"PDF exceeds the configured {self.options.max_file_bytes}-byte limit."
            )
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise InvalidPDFError("File does not begin with a PDF magic signature.")
        try:
            with pdfplumber.open(path) as pdf:
                encrypted = bool(getattr(pdf.doc, "encryption", None))
                if encrypted:
                    raise EncryptedPDFError("Encrypted PDFs are not accepted.")
                page_count = len(pdf.pages)
        except EncryptedPDFError:
            raise
        except PDFPasswordIncorrect as error:
            raise EncryptedPDFError("Encrypted PDFs are not accepted.") from error
        except Exception as error:
            error_name = type(error).__name__.casefold()
            wrapped_password_error = any(
                isinstance(argument, PDFPasswordIncorrect) for argument in error.args
            )
            if wrapped_password_error or "password" in error_name or "encrypt" in error_name:
                raise EncryptedPDFError("Encrypted PDFs are not accepted.") from error
            raise CorruptPDFError("PDF structure could not be read safely.") from error
        if page_count < 1:
            raise CorruptPDFError("PDF contains no pages.")
        if page_count > self.options.max_pages:
            raise PDFLimitError(
                f"PDF has {page_count} pages; the configured limit is {self.options.max_pages}."
            )
        return PDFInfo(page_count=page_count, byte_size=byte_size, encrypted=False)

    def parse(self, source_path: Path, *, artifact_dir: Path) -> ParsedDocument:
        path = source_path.resolve()
        self.validate(path)
        artifacts = artifact_dir.resolve()
        artifacts.mkdir(parents=True, exist_ok=True)
        drafts: list[_PageDraft] = []
        try:
            pdfium_document = pdfium.PdfDocument(str(path))
        except Exception as error:
            raise CorruptPDFError("PDF rendering engine could not open the document.") from error
        try:
            with pdfplumber.open(path) as pdf:
                for page_index, page in enumerate(pdf.pages):
                    page_number = page_index + 1
                    width = float(page.width)
                    height = float(page.height)
                    warnings: list[ParseWarning] = []
                    native_source_text = page.extract_text() or ""
                    tables = extract_tables(page, page_number=page_number)
                    elements: list[ParsedElement] = []
                    for block in _extract_text_blocks(page, tables):
                        elements.append(
                            ParsedElement(
                                page_number=page_number,
                                modality=ElementModality.TEXT,
                                content=block.content,
                                extraction_method="pdfplumber_words",
                                bbox=normalize_bbox(
                                    block.bbox,
                                    page_width=width,
                                    page_height=height,
                                ),
                                metadata={
                                    "source_bbox": list(block.bbox),
                                    "reading_order": len(elements),
                                    "directly_observed": True,
                                },
                            )
                        )
                    for table in tables:
                        elements.extend(
                            _table_elements(table, page_width=width, page_height=height)
                        )
                    rendered: Image.Image | None = None
                    page_asset_path: Path | None = None
                    try:
                        pdfium_page = pdfium_document[page_index]
                        try:
                            rendered = _render_page(pdfium_page, scale=self.options.render_scale)
                        finally:
                            pdfium_page.close()
                        page_asset_path = (artifacts / f"page-{page_number:03d}.png").resolve()
                        _save_png(rendered, page_asset_path)
                    except Exception:
                        warnings.append(
                            ParseWarning(
                                code="page_render_failed",
                                message=(
                                    "The page could not be rendered; native content remains "
                                    "available."
                                ),
                                page_number=page_number,
                            )
                        )
                    if rendered is not None:
                        elements.extend(
                            _visual_elements(
                                page=page,
                                page_number=page_number,
                                rendered=rendered,
                                native_text=native_source_text,
                                artifact_dir=artifacts,
                                minimum_area_ratio=self.options.minimum_visual_area_ratio,
                            )
                        )
                        native_character_count = len("".join(native_source_text.split()))
                        if native_character_count < self.options.minimum_native_characters:
                            ocr_result = self.ocr_processor.extract(
                                rendered,
                                page_number=page_number,
                            )
                            warnings.extend(ocr_result.warnings)
                            if ocr_result.text:
                                elements.append(
                                    ParsedElement(
                                        page_number=page_number,
                                        modality=ElementModality.OCR,
                                        content=ocr_result.text,
                                        extraction_method=ocr_result.extraction_method,
                                        bbox=(0.0, 0.0, 1.0, 1.0),
                                        confidence=ocr_result.confidence,
                                        asset_path=page_asset_path,
                                        metadata={
                                            "asset_key": page_asset_path.name
                                            if page_asset_path
                                            else None,
                                            "directly_observed": True,
                                        },
                                    )
                                )
                        rendered.close()
                    drafts.append(
                        _PageDraft(
                            page_number=page_number,
                            width=width,
                            height=height,
                            native_source_text=native_source_text,
                            elements=elements,
                            page_asset_path=page_asset_path,
                            warnings=warnings,
                        )
                    )
        except (EncryptedPDFError, PDFLimitError, InvalidPDFError, CorruptPDFError):
            raise
        except Exception as error:
            raise CorruptPDFError(
                "PDF parsing stopped on unreadable document structure."
            ) from error
        finally:
            pdfium_document.close()
        _suppress_repeated_furniture(drafts)
        pages: list[ParsedPage] = []
        all_warnings: list[ParseWarning] = []
        for draft in drafts:
            draft.elements.sort(key=_element_sort_key)
            native_text = "\n".join(
                element.content
                for element in draft.elements
                if element.modality
                in {ElementModality.TEXT, ElementModality.TABLE, ElementModality.TABLE_ROW}
            )
            pages.append(
                ParsedPage(
                    page_number=draft.page_number,
                    width=draft.width,
                    height=draft.height,
                    native_text=native_text,
                    elements=draft.elements,
                    page_asset_path=draft.page_asset_path,
                    warnings=draft.warnings,
                )
            )
            all_warnings.extend(draft.warnings)
        return ParsedDocument(
            source_path=path,
            sha256=sha256_file(path),
            pages=pages,
            warnings=all_warnings,
        )


def parse_pdf(
    source_path: Path,
    *,
    artifact_dir: Path,
    options: PDFParserOptions | None = None,
) -> ParsedDocument:
    """Convenience wrapper for callers that do not need processor injection."""

    return PDFParser(options).parse(source_path, artifact_dir=artifact_dir)
