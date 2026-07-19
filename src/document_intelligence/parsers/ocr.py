"""Optional, fail-soft Tesseract OCR adapter."""

from __future__ import annotations

import importlib
import importlib.util
import shutil
from dataclasses import dataclass
from statistics import fmean
from typing import Any, cast

from PIL import Image

from document_intelligence.parsers.base import ParseWarning


def _load_pytesseract() -> tuple[Any | None, str | None]:
    if importlib.util.find_spec("pytesseract") is None:
        return None, "The optional pytesseract package is not installed."
    if shutil.which("tesseract") is None:
        return None, "The Tesseract executable is not installed or not on PATH."
    return importlib.import_module("pytesseract"), None


@dataclass(frozen=True, slots=True)
class OCRResult:
    text: str | None
    confidence: float
    extraction_method: str
    warnings: tuple[ParseWarning, ...] = ()


class OCRProcessor:
    """Run Tesseract only when explicitly enabled and fully available."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        timeout_seconds: int = 30,
        backend: Any | None = None,
    ) -> None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        if backend is not None:
            self._backend = backend
            self._unavailable_reason = None
        elif enabled:
            self._backend, self._unavailable_reason = _load_pytesseract()
        else:
            self._backend = None
            self._unavailable_reason = "OCR is disabled by configuration."

    @property
    def available(self) -> bool:
        return self.enabled and self._backend is not None

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    def extract(
        self,
        image: Image.Image,
        *,
        page_number: int,
        page_segmentation_mode: int = 6,
    ) -> OCRResult:
        if not 3 <= page_segmentation_mode <= 13:
            raise ValueError("page_segmentation_mode must be between 3 and 13")
        if not self.available:
            return OCRResult(
                text=None,
                confidence=0.0,
                extraction_method="ocr_unavailable",
                warnings=(
                    ParseWarning(
                        code="ocr_unavailable" if self.enabled else "ocr_disabled",
                        message=self._unavailable_reason or "OCR is unavailable.",
                        page_number=page_number,
                    ),
                ),
            )
        backend = cast(Any, self._backend)
        try:
            output_type = backend.Output.DICT
            data = backend.image_to_data(
                image,
                output_type=output_type,
                timeout=self.timeout_seconds,
                config=f"--psm {page_segmentation_mode}",
            )
        except Exception as error:  # pytesseract exposes several backend-specific failures
            return OCRResult(
                text=None,
                confidence=0.0,
                extraction_method="tesseract_failed",
                warnings=(
                    ParseWarning(
                        code="ocr_failed",
                        message=(
                            f"OCR failed ({type(error).__name__}); other page content "
                            "remains usable."
                        ),
                        page_number=page_number,
                    ),
                ),
            )
        tokens: list[str] = []
        confidences: list[float] = []
        raw_text = data.get("text", []) if isinstance(data, dict) else []
        raw_confidence = data.get("conf", []) if isinstance(data, dict) else []
        for token, confidence in zip(raw_text, raw_confidence, strict=False):
            normalized = " ".join(str(token).split())
            if not normalized:
                continue
            tokens.append(normalized)
            try:
                numeric_confidence = float(confidence)
            except (TypeError, ValueError):
                continue
            if numeric_confidence >= 0:
                confidences.append(numeric_confidence / 100.0)
        if not tokens:
            return OCRResult(
                text=None,
                confidence=0.0,
                extraction_method="tesseract",
                warnings=(
                    ParseWarning(
                        code="ocr_no_text",
                        message="OCR completed but found no readable text.",
                        page_number=page_number,
                    ),
                ),
            )
        confidence = min(max(fmean(confidences) if confidences else 0.5, 0.0), 1.0)
        return OCRResult(
            text=" ".join(tokens),
            confidence=confidence,
            extraction_method="tesseract",
        )
