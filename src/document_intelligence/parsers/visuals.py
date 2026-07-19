"""Observed visual metadata and conservative visual-type classification."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageStat

from document_intelligence.parsers.base import ElementModality, NormalizedBBox

_CHART_TERMS = {
    "chart",
    "revenue",
    "product mix",
    "bookings",
    "legend",
    "bar",
    "trend",
}
_DIAGRAM_TERMS = {
    "diagram",
    "flow",
    "pressure test",
    "pressure-test",
    "process",
    "branch",
    "route",
}


@dataclass(frozen=True, slots=True)
class VisualClassification:
    modality: ElementModality
    method: str
    confidence: float


def _contains_term(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def classify_visual(
    *,
    surrounding_text: str,
    bbox: NormalizedBBox,
    native_character_count: int,
) -> VisualClassification:
    """Classify an image without presenting a heuristic as direct observation."""

    lowered = surrounding_text.casefold()
    if _contains_term(lowered, _DIAGRAM_TERMS):
        return VisualClassification(ElementModality.DIAGRAM, "context_heuristic", 0.9)
    if _contains_term(lowered, _CHART_TERMS):
        return VisualClassification(ElementModality.CHART, "context_heuristic", 0.88)
    area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
    if native_character_count < 40 and area >= 0.75:
        return VisualClassification(ElementModality.IMAGE, "full_page_scan_heuristic", 0.92)
    return VisualClassification(ElementModality.IMAGE, "generic_image", 0.65)


def observed_image_metadata(image: Image.Image) -> dict[str, object]:
    """Return bounded pixel facts; semantic meaning remains explicitly inferred."""

    rgb = image.convert("RGB")
    stats = ImageStat.Stat(rgb.resize((32, 32)))
    channel_means = tuple(round(value, 2) for value in stats.mean)
    return {
        "pixel_width": image.width,
        "pixel_height": image.height,
        "mode": rgb.mode,
        "channel_means": channel_means,
        "directly_observed_fields": ["bbox", "pixel_width", "pixel_height", "mode"],
        "inferred_fields": ["visual_type"],
    }


def visual_content(*, modality: ElementModality, page_number: int, surrounding_text: str) -> str:
    context = " ".join(surrounding_text.split())[:600]
    label = modality.value.capitalize()
    if context:
        return f"{label} visual on page {page_number}. Nearby document text: {context}"
    return f"{label} visual on page {page_number}; no native text was available."
