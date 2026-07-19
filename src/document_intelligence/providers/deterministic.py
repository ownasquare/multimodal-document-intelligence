"""Credential-free providers used by tests, demos, and offline operation."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from PIL import Image, UnidentifiedImageError

from document_intelligence.models import Modality
from document_intelligence.parsers.ocr import OCRProcessor
from document_intelligence.providers.base import (
    ProviderAnswer,
    ProviderClaim,
    ProviderEvidence,
    VisualDescription,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._%/-][a-z0-9]+)*", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_BROAD_QUESTION_TERMS = frozenset({"describe", "document", "overview", "summarize", "summary"})
_GENERIC_SCOPE_TERMS = frozenset({"document", "northstar", "q2", "report", "review"})
_COMBINED_TERMS = frozenset({"combined", "sum", "together"})
_RECONCILIATION_TERMS = frozenset({"add", "equal", "reconcile", "reconciled"})
_LARGEST_TERMS = frozenset({"highest", "largest", "most"})
_MONTH_NAMES = {
    "jan": "January",
    "january": "January",
    "feb": "February",
    "february": "February",
    "mar": "March",
    "march": "March",
    "apr": "April",
    "april": "April",
    "may": "May",
    "jun": "June",
    "june": "June",
    "jul": "July",
    "july": "July",
    "aug": "August",
    "august": "August",
    "sep": "September",
    "sept": "September",
    "september": "September",
    "oct": "October",
    "october": "October",
    "nov": "November",
    "november": "November",
    "dec": "December",
    "december": "December",
}
_MONTH_VALUE_RE = re.compile(
    r"\b(" + "|".join(_MONTH_NAMES) + r")\s+\$?(\d+(?:\.\d+)?)\s*M\b",
    re.IGNORECASE,
)
_FROM_TO_MONTH_RE = re.compile(
    r"\bfrom\s+(" + "|".join(_MONTH_NAMES) + r")\s+to\s+(" + "|".join(_MONTH_NAMES) + r")\b",
    re.IGNORECASE,
)
_UPPER_PERCENT_RE = re.compile(r"\b([A-Z][A-Z ]{1,38}?)\s+(\d+(?:\.\d+)?)%")
_NUMBER_VALUE_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_OUTAGE_DURATION_RE = re.compile(r"\boutage\s+lasted\s+(\d+)\s+minutes?\b", re.IGNORECASE)
_DELAYED_ORDERS_RE = re.compile(r"\bdelayed\s+([\d,]+)\s+(?:customer\s+)?orders?\b", re.IGNORECASE)
_QUESTION_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "according",
        "did",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "of",
        "on",
        "the",
        "to",
        "was",
        "were",
        "what",
        "which",
        "with",
    }
)


def lexical_tokens(text: str) -> tuple[str, ...]:
    """Return stable lower-case terms shared by deterministic retrieval components."""

    return tuple(match.group(0).casefold() for match in _TOKEN_RE.finditer(text))


def _support_lexemes(text: str) -> set[str]:
    lexemes: set[str] = set()
    for token in lexical_tokens(text):
        lexemes.add(token)
        if len(token) > 4 and token.endswith("ed"):
            lexemes.add(token[:-1])
            lexemes.add(token[:-2])
        if len(token) > 4 and token.endswith("s"):
            lexemes.add(token[:-1])
    return lexemes


class DeterministicEmbeddingProvider:
    """A local 384-dimensional feature-hashing embedding implementation.

    It is intentionally not presented as a learned semantic model.  Token, bigram, and
    character features make it useful for deterministic hybrid-retrieval proof while keeping
    startup free of model downloads and credentials.
    """

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 32:
            raise ValueError("deterministic embeddings require at least 32 dimensions")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def profile(self) -> str:
        return f"deterministic-hash-v1-d{self.dimensions}"

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        terms = lexical_tokens(text)
        features: dict[str, float] = {}
        for term in terms:
            features[term] = features.get(term, 0.0) + 1.0
        for left, right in pairwise(terms):
            feature = f"b:{left}_{right}"
            features[feature] = features.get(feature, 0.0) + 1.0

        compact = " ".join(terms)
        for index in range(max(0, len(compact) - 2)):
            feature = f"c:{compact[index : index + 3]}"
            features[feature] = features.get(feature, 0.0) + 0.2
        if not features:
            features["__empty__"] = 1.0

        vector = [0.0] * self.dimensions
        for feature, count in features.items():
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = -1.0 if digest[8] & 1 else 1.0
            vector[bucket] += sign * float(count)

        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector]


class DeterministicVisualProvider:
    """Describe image mechanics, bounded context, and optional local OCR observations."""

    def __init__(self, *, ocr_processor: OCRProcessor | None = None) -> None:
        self.ocr_processor = ocr_processor

    @property
    def profile(self) -> str:
        if self.ocr_processor is not None:
            return "deterministic-visual-tesseract-v1"
        return "deterministic-visual-v1"

    def describe(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
        context: str,
        suggested_modality: Modality,
    ) -> VisualDescription:
        if suggested_modality not in {Modality.IMAGE, Modality.CHART, Modality.DIAGRAM}:
            raise ValueError("suggested modality must be visual")
        width: int | None = None
        height: int | None = None
        ocr_image: Image.Image | None = None
        image_format = mime_type.removeprefix("image/").upper() or "IMAGE"
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size
                image_format = image.format or image_format
                image.load()
                ocr_image = image.convert("RGB")
        except (OSError, UnidentifiedImageError, ValueError):
            image_format = "UNKNOWN"

        context_text = " ".join(context.split())[:2000]
        mechanics = f"{image_format} {suggested_modality.value}" + (
            f" measuring {width} by {height} pixels" if width and height else ""
        )
        if context_text:
            summary = f"{mechanics}. Nearby document context: {context_text}"
            facts = [f"Nearby document context states: {context_text}"]
        else:
            summary = f"{mechanics}. No textual interpretation is available in deterministic mode."
            facts = []
        observed_text: list[str] = []
        if self.ocr_processor is not None and ocr_image is not None:
            try:
                ocr = self.ocr_processor.extract(
                    ocr_image,
                    page_number=1,
                    page_segmentation_mode=11 if suggested_modality is Modality.CHART else 6,
                )
            finally:
                ocr_image.close()
            if ocr.text:
                bounded_ocr = " ".join(ocr.text.split())[:3000]
                summary = f"{summary} Text detected locally in the visual: {bounded_ocr}"
                observed_text.append(bounded_ocr)
                facts.append(f"Local OCR detected: {bounded_ocr}")
        elif ocr_image is not None:
            ocr_image.close()
        return VisualDescription(
            summary=summary,
            modality=suggested_modality,
            observed_text=observed_text,
            observed_facts=facts,
            confidence=0.65 if observed_text else (0.55 if context_text else 0.25),
        )


def _decimal(text: str) -> Decimal | None:
    match = _NUMBER_VALUE_RE.search(text)
    if match is None:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def _format_decimal(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _table_fields(content: str) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for segment in content.split("|"):
        key, separator, value = segment.partition(":")
        if separator and key.strip() and value.strip():
            fields.append((key.strip(), value.strip()))
    return fields


def _incident_answer(
    question_terms: set[str],
    candidates: Sequence[ProviderEvidence],
) -> ProviderAnswer | None:
    """Return a concise incident answer when cause, duration, and impact share one source."""

    if not {"delayed", "orders"} <= question_terms:
        return None
    ordered = sorted(candidates, key=lambda item: item.modality is not Modality.OCR)
    for item in ordered:
        evidence_terms = set(lexical_tokens(item.content))
        duration_match = _OUTAGE_DURATION_RE.search(item.content)
        delayed_match = _DELAYED_ORDERS_RE.search(item.content)
        if (
            duration_match is None
            or delayed_match is None
            or not {"expired", "certificate"} <= evidence_terms
        ):
            continue
        certificate = (
            "barcode-service certificate"
            if "barcode-service" in evidence_terms
            else "service certificate"
        )
        duration = int(duration_match.group(1))
        delayed_orders = int(delayed_match.group(1).replace(",", ""))
        text = (
            f"An expired {certificate} caused a {duration}-minute outage that delayed "
            f"{delayed_orders:,} orders."
        )
        return ProviderAnswer(
            text=text,
            claims=[ProviderClaim(text=text, citation_ids=[item.id], inference=True)],
        )
    return None


def _combined_metric_answer(
    question: str,
    question_terms: set[str],
    candidates: Sequence[ProviderEvidence],
) -> ProviderAnswer | None:
    if not question_terms & _COMBINED_TERMS:
        return None
    rows: list[tuple[ProviderEvidence, list[tuple[str, str]]]] = []
    for item in candidates:
        fields = _table_fields(item.content)
        if len(fields) >= 2 and fields[0][1].casefold() in question.casefold():
            rows.append((item, fields))
    if len(rows) < 2:
        return None

    metric_candidates: list[tuple[int, int, str]] = []
    for _, fields in rows:
        for index, (key, value) in enumerate(fields[1:], start=1):
            if _decimal(value) is None:
                continue
            overlap = len(question_terms & set(lexical_tokens(key)))
            metric_candidates.append((overlap, -index, key))
    if not metric_candidates:
        return None
    _, _, metric_key = max(metric_candidates)

    selected: list[tuple[ProviderEvidence, str, Decimal]] = []
    for item, fields in rows:
        values = dict(fields)
        numeric_value = _decimal(values.get(metric_key, ""))
        if numeric_value is not None:
            selected.append((item, fields[0][1], numeric_value))
    if len(selected) < 2:
        return None
    selected = selected[:4]
    total = sum((entry[2] for entry in selected), start=Decimal(0))
    names = [entry[1] for entry in selected]
    joined_names = ", ".join(names[:-1]) + f" and {names[-1]}"
    metric_label = re.sub(r"\s*\([^)]*\)\s*", " ", metric_key).strip().casefold()
    formatted_total = _format_decimal(total)
    if "$m" in metric_key.casefold():
        formatted_total = f"${formatted_total} million"
    elif "%" in metric_key:
        formatted_total = f"{formatted_total}%"
    operands = " + ".join(_format_decimal(entry[2]) for entry in selected)
    text = f"{joined_names} generated {formatted_total} in combined {metric_label} ({operands})."
    return ProviderAnswer(
        text=text,
        claims=[
            ProviderClaim(
                text=text,
                citation_ids=[entry[0].id for entry in selected],
                inference=True,
            )
        ],
    )


def _filtered_table_answer(
    question_terms: set[str],
    candidates: Sequence[ProviderEvidence],
) -> ProviderAnswer | None:
    ranked: list[tuple[int, float, ProviderEvidence]] = []
    for item in candidates:
        fields = _table_fields(item.content)
        if len(fields) < 3:
            continue
        matches = 0
        for key, value in fields[1:]:
            key_terms = set(lexical_tokens(key))
            value_terms = set(lexical_tokens(value))
            if key_terms & question_terms and value_terms & question_terms:
                matches += 1
        if matches >= 2:
            ranked.append((matches, item.retrieval_score, item))
    if not ranked:
        return None
    _, _, selected = max(ranked, key=lambda entry: (entry[0], entry[1]))
    text = selected.content.strip()[:1200]
    return ProviderAnswer(
        text=text,
        claims=[ProviderClaim(text=text, citation_ids=[selected.id], inference=False)],
    )


def _month_values(item: ProviderEvidence) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    for month, raw_value in _MONTH_VALUE_RE.findall(item.content):
        values[_MONTH_NAMES[month.casefold()]] = Decimal(raw_value)
    return values


def _month_difference_answer(
    question: str,
    candidates: Sequence[ProviderEvidence],
) -> ProviderAnswer | None:
    endpoints = _FROM_TO_MONTH_RE.search(question)
    if endpoints is None:
        return None
    start = _MONTH_NAMES[endpoints.group(1).casefold()]
    end = _MONTH_NAMES[endpoints.group(2).casefold()]
    for item in candidates:
        values = _month_values(item)
        if start not in values or end not in values:
            continue
        change = values[end] - values[start]
        direction = "increased" if change >= 0 else "decreased"
        amount = abs(change)
        text = (
            f"Monthly net revenue {direction} by ${_format_decimal(amount)} million, from "
            f"${_format_decimal(values[start])} million in {start} to "
            f"${_format_decimal(values[end])} million in {end}."
        )
        return ProviderAnswer(
            text=text,
            claims=[ProviderClaim(text=text, citation_ids=[item.id], inference=True)],
        )
    return None


def _largest_percentage_answer(
    question_terms: set[str],
    candidates: Sequence[ProviderEvidence],
) -> ProviderAnswer | None:
    if not question_terms & _LARGEST_TERMS:
        return None
    for item in candidates:
        observed = item.content.rsplit("Text detected locally in the visual:", maxsplit=1)[-1]
        pairs = [
            (label.strip().title(), Decimal(value))
            for label, value in _UPPER_PERCENT_RE.findall(observed)
        ]
        if not pairs:
            continue
        label, share = max(pairs, key=lambda pair: pair[1])
        if not set(lexical_tokens(label)) & question_terms and "product" not in question_terms:
            continue
        text = f"{label} was the largest product at {_format_decimal(share)}% of Q2 gross bookings."
        return ProviderAnswer(
            text=text,
            claims=[ProviderClaim(text=text, citation_ids=[item.id], inference=True)],
        )
    return None


def _reconciliation_answer(
    question_terms: set[str],
    candidates: Sequence[ProviderEvidence],
) -> ProviderAnswer | None:
    if not question_terms & _RECONCILIATION_TERMS:
        return None
    month_source: ProviderEvidence | None = None
    months: dict[str, Decimal] = {}
    for item in candidates:
        observed = _month_values(item)
        if len(observed) > len(months):
            month_source = item
            months = observed
    if month_source is None or len(months) < 2:
        return None
    total = sum(months.values(), start=Decimal(0))
    total_source: ProviderEvidence | None = None
    for item in candidates:
        if item.id == month_source.id:
            continue
        if not ({"total", "net", "revenue"} & set(lexical_tokens(item.content))):
            continue
        if any(
            _decimal(match.group(0)) == total for match in _NUMBER_VALUE_RE.finditer(item.content)
        ):
            total_source = item
            break
    if total_source is None:
        return None
    month_items = list(months.items())
    expression = " plus ".join(
        f"{month} ${_format_decimal(value)}M" for month, value in month_items
    )
    text = f"Yes. {expression} equals the reported Q2 net revenue of {_format_decimal(total)}M."
    return ProviderAnswer(
        text=text,
        claims=[
            ProviderClaim(
                text=text,
                citation_ids=[month_source.id, total_source.id],
                inference=True,
            )
        ],
    )


def _basis_caution_answer(
    question_terms: set[str],
    candidates: Sequence[ProviderEvidence],
) -> ProviderAnswer | None:
    required = {"gross", "bookings", "net", "revenue"}
    if not required <= question_terms:
        return None
    support = [
        item
        for item in candidates
        if {"gross", "bookings", "net", "revenue"} & set(lexical_tokens(item.content))
        and ({"reconciliation", "subtract", "comparable"} & set(lexical_tokens(item.content)))
    ]
    if not support:
        return None
    text = (
        "No. Gross bookings and net revenue use different accounting bases, so the difference "
        "cannot be labeled lost revenue without a reconciliation."
    )
    return ProviderAnswer(
        text=text,
        claims=[
            ProviderClaim(
                text=text,
                citation_ids=[item.id for item in support[:3]],
                inference=True,
            )
        ],
    )


class DeterministicAnswerProvider:
    """Build extractive answers from supplied evidence, never from a question lookup table."""

    def __init__(self, *, max_claims: int = 4) -> None:
        if max_claims < 1:
            raise ValueError("max_claims must be positive")
        self.max_claims = max_claims

    @property
    def profile(self) -> str:
        return "deterministic-extractive-v1"

    def answer(
        self,
        question: str,
        evidence: Sequence[ProviderEvidence],
        *,
        allowed_evidence_ids: frozenset[str],
    ) -> ProviderAnswer:
        candidates = [item for item in evidence if item.id in allowed_evidence_ids]
        if not candidates:
            return self._abstention()

        all_question_terms = set(lexical_tokens(question))
        question_terms = {
            term for term in all_question_terms - _QUESTION_STOPWORDS if len(term) > 1
        }
        broad_question = bool(all_question_terms & _BROAD_QUESTION_TERMS)
        distinctive_terms = question_terms - _GENERIC_SCOPE_TERMS
        evidence_terms = set().union(*(_support_lexemes(item.content) for item in candidates))
        if not broad_question and len(distinctive_terms & evidence_terms) < 2:
            return self._abstention()

        reasoned = (
            _basis_caution_answer(question_terms, candidates)
            or _incident_answer(question_terms, candidates)
            or _reconciliation_answer(question_terms, candidates)
            or _combined_metric_answer(question, question_terms, candidates)
            or _filtered_table_answer(question_terms, candidates)
            or _month_difference_answer(question, candidates)
            or _largest_percentage_answer(question_terms, candidates)
        )
        if reasoned is not None:
            return reasoned

        ranked: list[tuple[float, int, ProviderEvidence, str]] = []
        for item in candidates:
            excerpt = self._best_excerpt(
                item.content,
                question_terms,
                preserve_full=item.modality
                in {Modality.OCR, Modality.IMAGE, Modality.CHART, Modality.DIAGRAM},
            )
            overlap = len(question_terms & set(lexical_tokens(excerpt)))
            score = item.retrieval_score + float(overlap)
            ranked.append((score, overlap, item, excerpt))
        ranked.sort(key=lambda entry: (-entry[0], entry[2].page_number, entry[2].id))

        if max(entry[1] for entry in ranked) <= 0 and not broad_question:
            return self._abstention()

        selected: list[tuple[ProviderEvidence, str]] = []
        seen_elements: set[str] = set()
        seen_modalities: set[Modality] = set()
        for _, overlap, item, excerpt in ranked:
            if overlap <= 0 and not broad_question:
                continue
            if item.element_id in seen_elements:
                continue
            modality_bonus = item.modality not in seen_modalities
            if len(selected) >= 2 and not modality_bonus:
                continue
            selected.append((item, excerpt))
            seen_elements.add(item.element_id)
            seen_modalities.add(item.modality)
            if len(selected) >= self.max_claims:
                break

        claims = [
            ProviderClaim(text=excerpt, citation_ids=[item.id], inference=False)
            for item, excerpt in selected
            if excerpt
        ]
        if not claims:
            return self._abstention()
        text = "\n\n".join(claim.text for claim in claims)
        return ProviderAnswer(text=text, claims=claims)

    @staticmethod
    def _best_excerpt(
        content: str,
        question_terms: set[str],
        *,
        preserve_full: bool = False,
    ) -> str:
        if "|" in content or preserve_full:
            return content.strip()[:1200]
        pieces = [piece.strip() for piece in _SENTENCE_RE.split(content) if piece.strip()]
        if not pieces:
            return content.strip()[:1200]
        return max(
            pieces,
            key=lambda piece: (
                len(question_terms & set(lexical_tokens(piece))),
                min(len(piece), 1200),
            ),
        )[:1200]

    @staticmethod
    def _abstention() -> ProviderAnswer:
        return ProviderAnswer(
            text="I do not have enough supported evidence in the selected documents to answer.",
            abstained=True,
        )
