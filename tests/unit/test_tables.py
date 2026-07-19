from __future__ import annotations

from pathlib import Path

from document_intelligence.parsers import ElementModality, PDFParser, PDFParserOptions
from document_intelligence.parsers.ocr import OCRProcessor
from document_intelligence.parsers.tables import normalize_cell, structure_table

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PDF = PROJECT_ROOT / "examples" / "northstar-q2-operations-review.pdf"


def test_structure_table_normalizes_headers_rows_units_and_notes() -> None:
    table = structure_table(
        [
            ["Region", "Revenue\n($M)", "", "Region"],
            ["  West ", " 2.3 ", None, "Primary"],
            ["", "", "", ""],
        ],
        page_number=2,
        bbox=(10.0, 20.0, 200.0, 120.0),
        caption="Regional performance",
        footnotes=("Rounded values",),
    )

    assert table is not None
    assert table.headers == ("Region", "Revenue ($M)", "column_3", "Region_2")
    assert table.rows == (
        {"Region": "West", "Revenue ($M)": "2.3", "column_3": "", "Region_2": "Primary"},
    )
    assert table.units == {"Revenue ($M)": "$M"}
    assert "Rounded values" in table.summary_text()
    assert table.row_text(0).startswith("Region: West | Revenue ($M): 2.3")
    assert normalize_cell("a\n  b\t c") == "a b c"


def test_sample_tables_emit_summary_and_row_provenance(tmp_path: Path) -> None:
    result = PDFParser(
        PDFParserOptions(render_scale=0.75, enable_ocr=False),
        ocr_processor=OCRProcessor(enabled=False),
    ).parse(SAMPLE_PDF, artifact_dir=tmp_path / "assets")

    regional_table = next(
        element for element in result.pages[1].elements if element.modality is ElementModality.TABLE
    )
    regional_rows = [
        element
        for element in result.pages[1].elements
        if element.modality is ElementModality.TABLE_ROW
    ]
    west = next(element for element in regional_rows if "Region: West" in element.content)
    assert len(regional_rows) == 5
    assert regional_table.metadata["headers"] == [
        "Region",
        "Net revenue ($M)",
        "Orders",
        "On-time (%)",
        "Return (%)",
    ]
    assert regional_table.metadata["units"] == {
        "Net revenue ($M)": "$M",
        "On-time (%)": "%",
        "Return (%)": "%",
    }
    assert west.metadata["values"]["Net revenue ($M)"] == "2.3"
    assert west.metadata["values"]["On-time (%)"] == "95.4"
    assert west.page_number == 2
    assert west.bbox == regional_table.bbox

    risk_rows = [
        element
        for element in result.pages[6].elements
        if element.modality is ElementModality.TABLE_ROW
    ]
    carrier = next(element for element in risk_rows if "R-03" in element.content)
    assert carrier.metadata["values"]["Likelihood"] == "High"
    assert carrier.metadata["values"]["Impact"] == "High"
    assert "Reserve 12% partner capacity by Aug 15" in carrier.content
