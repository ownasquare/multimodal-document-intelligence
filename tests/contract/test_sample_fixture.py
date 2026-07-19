from __future__ import annotations

import json
from pathlib import Path

import pdfplumber

from scripts.generate_sample import build_sample

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PDF = PROJECT_ROOT / "examples" / "northstar-q2-operations-review.pdf"
GOLDEN_QUESTIONS = PROJECT_ROOT / "tests" / "fixtures" / "golden_questions.json"


def test_fixture_is_exactly_eight_pages_with_expected_native_content() -> None:
    with pdfplumber.open(SAMPLE_PDF) as document:
        assert len(document.pages) == 8
        page_text = [(page.extract_text() or "") for page in document.pages]

    assert "Q2 Operations Review" in page_text[0]
    assert "Regional Performance" in page_text[1]
    assert "Monthly Revenue" in page_text[2]
    assert "Product Mix" in page_text[3]
    assert page_text[4].strip() == ""
    assert "Capacity Pressure Test" in page_text[5]
    assert "Risk Register" in page_text[6]
    assert "Appendix: Measurement Basis" in page_text[7]


def test_fixture_contains_raster_charts_scan_and_diagram() -> None:
    with pdfplumber.open(SAMPLE_PDF) as document:
        image_counts = [len(page.images) for page in document.pages]
        scan = document.pages[4].images[0]

    assert image_counts == [0, 0, 1, 1, 1, 1, 0, 0]
    assert float(scan["x0"]) == 0
    assert float(scan["top"]) == 0
    assert float(scan["x1"]) == 612
    assert float(scan["bottom"]) == 792


def test_fixture_generation_is_byte_stable_and_checked_in_output_matches(tmp_path: Path) -> None:
    first = build_sample(tmp_path / "first.pdf")
    second = build_sample(tmp_path / "second.pdf")

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() == SAMPLE_PDF.read_bytes()


def test_golden_questions_cover_the_required_reasoning_modes() -> None:
    questions = json.loads(GOLDEN_QUESTIONS.read_text(encoding="utf-8"))

    assert len(questions) == 10
    assert len({question["id"] for question in questions}) == 10
    assert sum(not question["answerable"] for question in questions) == 1
    modalities = {
        modality for question in questions for modality in question["required_modalities"]
    }
    reasoning = " ".join(question["reasoning"] for question in questions).casefold()
    assert {"text", "table", "table_row", "chart", "ocr", "image", "diagram"} <= modalities
    assert "arithmetic" not in reasoning or "add" in reasoning
    assert "incompatible" in reasoning
    assert "abstention" in reasoning
    for question in questions:
        assert set(question) == {
            "id",
            "question",
            "answerable",
            "expected_answer",
            "expected_facts",
            "required_modalities",
            "evidence_pages",
            "reasoning",
        }
        assert all(1 <= page <= 8 for page in question["evidence_pages"])
