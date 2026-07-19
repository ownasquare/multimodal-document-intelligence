from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from document_intelligence.config import Settings


@pytest.fixture(scope="session")
def live_openai_settings() -> Settings:
    """Load live-provider settings only after an explicit process-level opt in."""

    if os.getenv("DOCINTEL_RUN_LIVE_TESTS") != "1":
        pytest.skip("set DOCINTEL_RUN_LIVE_TESTS=1 to opt in to live provider tests")
    try:
        settings = Settings(
            provider_mode="openai",
            embedding_provider="openai",
            provider_timeout_seconds=45.0,
            provider_max_attempts=1,
        )
    except ValidationError:
        pytest.skip("DOCINTEL_OPENAI_API_KEY is required for live provider tests")
    if settings.openai_api_key is None:
        pytest.skip("DOCINTEL_OPENAI_API_KEY is required for live provider tests")
    return settings
