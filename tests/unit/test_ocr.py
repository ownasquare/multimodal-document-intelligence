from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from document_intelligence.parsers import ElementModality, PDFParser, PDFParserOptions
from document_intelligence.parsers import ocr as ocr_module
from document_intelligence.parsers.ocr import OCRProcessor

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PDF = PROJECT_ROOT / "examples" / "northstar-q2-operations-review.pdf"


class _SuccessfulBackend:
    Output = SimpleNamespace(DICT="dict")

    def __init__(self) -> None:
        self.calls = 0
        self.configs: list[str] = []

    def image_to_data(self, image: Image.Image, **kwargs: object) -> dict[str, list[object]]:
        self.calls += 1
        assert image.width > 0
        assert kwargs["output_type"] == "dict"
        assert kwargs["timeout"] == 7
        self.configs.append(str(kwargs["config"]))
        return {
            "text": ["Expired", " certificate ", "", "47", "minutes"],
            "conf": ["96", "94", "-1", "90", "bad"],
        }


class _FailingBackend:
    Output = SimpleNamespace(DICT="dict")

    def image_to_data(self, image: Image.Image, **kwargs: object) -> dict[str, list[object]]:
        raise RuntimeError("sensitive backend detail must not escape")


def test_missing_tesseract_returns_visible_warning(monkeypatch: object) -> None:
    monkeypatch.setattr(
        ocr_module,
        "_load_pytesseract",
        lambda: (None, "The Tesseract executable is not installed or not on PATH."),
    )
    processor = OCRProcessor(enabled=True)
    result = processor.extract(Image.new("RGB", (30, 30), "white"), page_number=5)

    assert processor.available is False
    assert result.text is None
    assert result.extraction_method == "ocr_unavailable"
    assert result.warnings[0].code == "ocr_unavailable"
    assert result.warnings[0].page_number == 5
    assert "not installed" in result.warnings[0].message


def test_successful_ocr_normalizes_tokens_and_confidence() -> None:
    backend = _SuccessfulBackend()
    result = OCRProcessor(enabled=True, timeout_seconds=7, backend=backend).extract(
        Image.new("RGB", (30, 30), "white"),
        page_number=5,
    )

    assert result.text == "Expired certificate 47 minutes"
    assert result.confidence == pytest.approx(0.9333333333333333)
    assert result.extraction_method == "tesseract"
    assert result.warnings == ()
    assert backend.calls == 1
    assert backend.configs == ["--psm 6"]


def test_ocr_accepts_bounded_sparse_text_mode() -> None:
    backend = _SuccessfulBackend()
    OCRProcessor(enabled=True, timeout_seconds=7, backend=backend).extract(
        Image.new("RGB", (30, 30), "white"),
        page_number=3,
        page_segmentation_mode=11,
    )

    assert backend.configs == ["--psm 11"]


def test_ocr_rejects_unbounded_page_segmentation_mode() -> None:
    with pytest.raises(ValueError, match="between 3 and 13"):
        OCRProcessor(enabled=True, backend=_SuccessfulBackend()).extract(
            Image.new("RGB", (30, 30), "white"),
            page_number=3,
            page_segmentation_mode=14,
        )


def test_ocr_failure_is_sanitized_to_warning() -> None:
    result = OCRProcessor(enabled=True, backend=_FailingBackend()).extract(
        Image.new("RGB", (30, 30), "white"),
        page_number=5,
    )

    assert result.text is None
    assert result.warnings[0].code == "ocr_failed"
    assert "RuntimeError" in result.warnings[0].message
    assert "sensitive backend detail" not in result.warnings[0].message


def test_scanned_page_routes_to_ocr_while_born_digital_pages_do_not(tmp_path: Path) -> None:
    backend = _SuccessfulBackend()
    parser = PDFParser(
        PDFParserOptions(render_scale=0.75, enable_ocr=True, ocr_timeout_seconds=7),
        ocr_processor=OCRProcessor(enabled=True, timeout_seconds=7, backend=backend),
    )
    result = parser.parse(SAMPLE_PDF, artifact_dir=tmp_path / "assets")

    assert backend.calls == 1
    ocr_elements = [
        element for element in result.elements if element.modality is ElementModality.OCR
    ]
    assert len(ocr_elements) == 1
    assert ocr_elements[0].page_number == 5
    assert "Expired certificate" in ocr_elements[0].content
    assert result.warnings == []
