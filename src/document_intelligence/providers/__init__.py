"""Provider contracts and built-in deterministic/OpenAI implementations."""

from document_intelligence.providers.base import (
    AnswerProvider,
    EmbeddingProvider,
    ProviderAnswer,
    ProviderClaim,
    ProviderError,
    ProviderEvidence,
    VisualDescription,
    VisualUnderstandingProvider,
)
from document_intelligence.providers.deterministic import (
    DeterministicAnswerProvider,
    DeterministicEmbeddingProvider,
    DeterministicVisualProvider,
)
from document_intelligence.providers.openai import (
    OpenAIAnswerProvider,
    OpenAIEmbeddingProvider,
    OpenAIVisualProvider,
    image_data_url,
)

__all__ = [
    "AnswerProvider",
    "DeterministicAnswerProvider",
    "DeterministicEmbeddingProvider",
    "DeterministicVisualProvider",
    "EmbeddingProvider",
    "OpenAIAnswerProvider",
    "OpenAIEmbeddingProvider",
    "OpenAIVisualProvider",
    "ProviderAnswer",
    "ProviderClaim",
    "ProviderError",
    "ProviderEvidence",
    "VisualDescription",
    "VisualUnderstandingProvider",
    "image_data_url",
]
