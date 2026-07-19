"""Configuration safety and default-contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from document_intelligence.config import Settings


def test_defaults_are_loopback_deterministic_and_bounded(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8014
    assert settings.ui_port == 8514
    assert settings.is_loopback is True
    assert settings.provider_mode == "deterministic"
    assert settings.embedding_provider == "deterministic"
    assert settings.max_file_bytes == 50 * 1024 * 1024
    assert settings.max_pages == 250
    assert settings.max_upload_batch == 10
    assert settings.worker_concurrency <= 2


def test_non_loopback_binding_requires_application_token(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="API_TOKEN"):
        Settings(data_dir=tmp_path, api_host="0.0.0.0")  # noqa: S104 - validation target

    settings = Settings(
        data_dir=tmp_path,
        api_host="0.0.0.0",  # noqa: S104 - validation target
        api_token="safe-test-token",
    )
    assert settings.is_loopback is False


@pytest.mark.parametrize("field", ["provider_mode", "embedding_provider"])
def test_openai_paths_require_server_side_key(tmp_path: Path, field: str) -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(data_dir=tmp_path, **{field: "openai"})


def test_directories_are_private_and_derived_from_data_root(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "state")
    settings.ensure_directories()

    assert settings.database_path.parent == settings.data_dir
    assert settings.uploads_dir.is_dir()
    assert settings.artifacts_dir.is_dir()
    assert settings.chroma_dir.is_dir()
    assert settings.resolved_api_base_url == "http://127.0.0.1:8014"


def test_secret_values_are_redacted_in_representation(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        api_token="test-application-token-value",
        openai_api_key="test-provider-key-value",
    )
    representation = repr(settings)

    assert "test-application-token-value" not in representation
    assert "test-provider-key-value" not in representation
    assert "**********" in representation
