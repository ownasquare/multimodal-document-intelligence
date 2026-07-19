"""Bounded PDF, table, visual, and optional OCR parsing."""

from document_intelligence.parsers.base import (
    CorruptPDFError,
    ElementModality,
    EncryptedPDFError,
    InvalidPDFError,
    ParsedDocument,
    ParsedElement,
    ParsedPage,
    ParseWarning,
    PDFLimitError,
    PDFParserOptions,
)
from document_intelligence.parsers.ocr import OCRProcessor, OCRResult
from document_intelligence.parsers.pdf import PDFParser, parse_pdf

__all__ = [
    "CorruptPDFError",
    "ElementModality",
    "EncryptedPDFError",
    "InvalidPDFError",
    "OCRProcessor",
    "OCRResult",
    "PDFLimitError",
    "PDFParser",
    "PDFParserOptions",
    "ParseWarning",
    "ParsedDocument",
    "ParsedElement",
    "ParsedPage",
    "parse_pdf",
]
