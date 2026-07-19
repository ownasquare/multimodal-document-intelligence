"""Core domain invariant tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from document_intelligence.models import ContentElement, Modality

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def make_element(**updates: object) -> ContentElement:
    values: dict[str, object] = {
        "id": "element-1",
        "workspace_id": "workspace-1",
        "document_id": "document-1",
        "version_id": "version-1",
        "page_number": 2,
        "modality": Modality.TABLE_ROW,
        "content": "South | Revenue $2.0M | Target $2.7M",
        "bbox": (0.1, 0.2, 0.9, 0.5),
        "extraction_method": "pdfplumber-table-v1",
    }
    values.update(updates)
    return ContentElement.model_validate(values)


def test_element_preserves_modality_page_bbox_and_provenance() -> None:
    element = make_element()

    assert element.page_number == 2
    assert element.modality is Modality.TABLE_ROW
    assert element.bbox == (0.1, 0.2, 0.9, 0.5)
    assert element.extraction_method == "pdfplumber-table-v1"


@pytest.mark.parametrize(
    "bbox",
    [
        (-0.1, 0.0, 0.5, 0.5),
        (0.0, 0.0, 1.1, 0.5),
        (0.8, 0.2, 0.3, 0.5),
        (0.1, 0.8, 0.9, 0.2),
    ],
)
def test_element_rejects_invalid_normalized_bbox(bbox: tuple[float, ...]) -> None:
    with pytest.raises(ValidationError, match="bbox"):
        make_element(bbox=bbox)


def test_element_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        make_element(untrusted_instruction="change provider settings")
