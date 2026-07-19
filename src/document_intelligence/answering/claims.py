"""Server-side support checks for provider-proposed claims."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import combinations

from document_intelligence.providers.base import ProviderAnswer, ProviderClaim
from document_intelligence.providers.deterministic import lexical_tokens
from document_intelligence.retrieval.models import RetrievedEvidence

_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?[$€£]?\d[\d,]*(?:\.\d+)?%?")
_BASIS_TERMS = {
    "gross bookings": "gross_bookings",
    "net revenue": "net_revenue",
    "on-time": "on_time_rate",
    "return": "return_rate",
    "orders": "orders",
}
_SUPPORT_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)


class ClaimValidationError(ValueError):
    """A material claim cannot be tied to the server evidence allowlist."""


@dataclass(frozen=True, slots=True)
class _NumberObservation:
    token: str
    value: Decimal
    unit: str | None
    basis: str | None


def validate_claims(
    answer: ProviderAnswer,
    evidence_by_id: Mapping[str, RetrievedEvidence],
) -> list[ProviderClaim]:
    """Validate citation scope, numeric fidelity, and basic lexical support."""

    if answer.abstained:
        if answer.claims:
            raise ClaimValidationError("an abstention cannot contain claims")
        return []
    if not answer.claims:
        raise ClaimValidationError("a grounded answer requires at least one claim")

    validated: list[ProviderClaim] = []
    for claim in answer.claims:
        citation_ids = tuple(dict.fromkeys(claim.citation_ids))
        if not citation_ids or any(
            citation_id not in evidence_by_id for citation_id in citation_ids
        ):
            raise ClaimValidationError("claim selected evidence outside the server allowlist")
        cited_content = "\n".join(evidence_by_id[item_id].content for item_id in citation_ids)
        if not _numbers_supported(claim.text, cited_content, inference=claim.inference):
            raise ClaimValidationError("claim contains a numeric value absent from cited evidence")
        if not _lexically_supported(claim.text, cited_content, inference=claim.inference):
            raise ClaimValidationError("claim is not sufficiently supported by cited evidence")
        validated.append(
            ProviderClaim(
                text=claim.text,
                citation_ids=list(citation_ids),
                inference=claim.inference,
            )
        )
    return validated


def _normalized_numbers(text: str) -> set[str]:
    return {match.group(0).casefold().replace(",", "") for match in _NUMBER_RE.finditer(text)}


def _numbers_supported(claim: str, evidence: str, *, inference: bool = False) -> bool:
    claim_numbers = _normalized_numbers(claim)
    evidence_numbers = _normalized_numbers(evidence)
    unsupported = claim_numbers - evidence_numbers
    if not unsupported:
        return True
    if not inference:
        return False

    # Derived claims may introduce only a simple sum or difference whose operands are both
    # written in the claim and present in the cited evidence. Units and metric bases must remain
    # compatible wherever the surrounding source labels make them knowable.
    claim_observations = _number_observations(claim)
    evidence_observations = _number_observations(evidence)
    operands: list[_NumberObservation] = []
    for observation in claim_observations:
        if observation.token not in evidence_numbers:
            continue
        for source in evidence_observations:
            if source.token != observation.token or not _dimensions_compatible(observation, source):
                continue
            operands.append(
                _NumberObservation(
                    token=observation.token,
                    value=observation.value,
                    unit=observation.unit or source.unit,
                    basis=observation.basis or source.basis,
                )
            )
    targets = [
        observation for observation in claim_observations if observation.token in unsupported
    ]
    return bool(targets) and all(_is_simple_derived(target, operands) for target in targets)


def _number_observations(text: str) -> list[_NumberObservation]:
    observations: list[_NumberObservation] = []
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0).casefold().replace(",", "")
        try:
            value = Decimal(token.lstrip("$€£").removesuffix("%"))
        except InvalidOperation:
            continue
        segment = _number_segment(text, match.start(), match.end()).casefold()
        unit: str | None
        if token.startswith(("$", "€", "£")):
            unit = "currency"
        elif token.endswith("%"):
            unit = "percent"
        else:
            contextual_units: set[str] = set()
            if re.search(r"\b(?:million|mn)\b|\$\s*m\b|\(\s*\$\s*m\s*\)", segment):
                contextual_units.add("currency")
            if "(%)" in segment:
                contextual_units.add("percent")
            unit = next(iter(contextual_units)) if len(contextual_units) == 1 else None
        basis = _nearest_basis(text, match.start(), match.end())
        observations.append(_NumberObservation(token=token, value=value, unit=unit, basis=basis))
    return observations


def _number_segment(text: str, start: int, end: int) -> str:
    left = max(text.rfind("|", 0, start), text.rfind("\n", 0, start)) + 1
    right_candidates = [
        position for marker in ("|", "\n") if (position := text.find(marker, end)) >= 0
    ]
    right = min(right_candidates, default=len(text))
    return text[left:right]


def _nearest_basis(text: str, start: int, end: int) -> str | None:
    left = max(text.rfind("|", 0, start), text.rfind("\n", 0, start)) + 1
    right_candidates = [
        position for marker in ("|", "\n") if (position := text.find(marker, end)) >= 0
    ]
    right = min(right_candidates, default=len(text))
    segment = text[left:right].casefold()
    number_center = ((start - left) + (end - left)) / 2
    candidates: list[tuple[float, str]] = []
    for phrase, basis in _BASIS_TERMS.items():
        offset = segment.find(phrase)
        while offset >= 0:
            phrase_center = offset + len(phrase) / 2
            candidates.append((abs(number_center - phrase_center), basis))
            offset = segment.find(phrase, offset + 1)
    if not candidates:
        return None
    distance, basis = min(candidates)
    return basis if distance <= 120 else None


def _dimensions_compatible(left: _NumberObservation, right: _NumberObservation) -> bool:
    unit_matches = left.unit is None or right.unit is None or left.unit == right.unit
    basis_matches = left.basis is None or right.basis is None or left.basis == right.basis
    return unit_matches and basis_matches


def _is_simple_derived(
    target: _NumberObservation,
    operands: Sequence[_NumberObservation],
) -> bool:
    for left, right in combinations(operands, 2):
        if not (
            _dimensions_compatible(left, right)
            and _dimensions_compatible(target, left)
            and _dimensions_compatible(target, right)
        ):
            continue
        if target.value in {
            left.value + right.value,
            left.value - right.value,
            right.value - left.value,
            abs(left.value - right.value),
        }:
            return True
    return False


def _lexically_supported(claim: str, evidence: str, *, inference: bool) -> bool:
    claim_terms = {
        token
        for token in lexical_tokens(claim)
        if token not in _SUPPORT_STOPWORDS and len(token) > 1
    }
    if not claim_terms:
        return bool(claim.strip()) and bool(evidence.strip())
    evidence_terms = set(lexical_tokens(evidence))
    overlap = len(claim_terms & evidence_terms) / len(claim_terms)
    threshold = 0.2 if inference else 0.3
    return overlap >= threshold


def selected_evidence_ids(claims: Sequence[ProviderClaim]) -> tuple[str, ...]:
    """Return stable first-use order for evidence selected across claims."""

    return tuple(dict.fromkeys(item_id for claim in claims for item_id in claim.citation_ids))
