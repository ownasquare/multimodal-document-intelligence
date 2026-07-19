"""Managed-file safety and deletion readback tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from document_intelligence.storage import (
    FileStorage,
    StorageDeletionError,
    StorageIntegrityError,
    UnsafeStoragePathError,
    sanitize_display_name,
)


@pytest.fixture
def storage(tmp_path: Path) -> FileStorage:
    result = FileStorage(tmp_path / "uploads", tmp_path / "artifacts")
    result.initialize()
    return result


def test_content_addressed_files_are_atomic_private_and_readable(storage: FileStorage) -> None:
    payload = b"%PDF-1.7\nmanaged bytes"
    expected = hashlib.sha256(payload).hexdigest()

    upload = storage.store_upload(
        "workspace-1",
        "version-1",
        payload,
        expected_sha256=expected,
        max_bytes=1_000,
    )
    artifact = storage.store_artifact("workspace-1", "version-1", "page-1", b"image", suffix=".png")

    assert upload.key == f"workspace-1/version-1/{expected}.pdf"
    assert storage.read_upload(upload.key) == payload
    assert storage.read_artifact(artifact.key) == b"image"
    assert upload.path.stat().st_mode & 0o777 == 0o600


def test_display_names_never_become_storage_paths() -> None:
    assert sanitize_display_name("../../Quarterly Report.pdf") == "Quarterly Report.pdf"
    assert sanitize_display_name("..\\..\\chart.pdf") == "chart.pdf"
    assert sanitize_display_name("\x00  ") == "document.pdf"


@pytest.mark.parametrize(
    "key",
    [
        "../outside.pdf",
        "/absolute/file.pdf",
        "workspace-1/../file.pdf",
        "workspace-1/version-1/not-a-digest.pdf",
        "workspace-1\\version-1\\file.pdf",
    ],
)
def test_persisted_key_traversal_is_rejected(storage: FileStorage, key: str) -> None:
    with pytest.raises(UnsafeStoragePathError):
        storage.resolve_upload_key(key, require_exists=False)


def test_symlink_ancestor_is_rejected(storage: FileStorage, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (storage.artifacts_root / "workspace-1").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeStoragePathError, match="symbolic link"):
        storage.store_artifact("workspace-1", "version-1", "page-1", b"image")

    assert list(outside.iterdir()) == []


def test_hash_mismatch_never_promotes_partial_bytes(storage: FileStorage) -> None:
    with pytest.raises(StorageIntegrityError, match="SHA-256"):
        storage.store_upload(
            "workspace-1",
            "version-1",
            b"wrong bytes",
            expected_sha256="0" * 64,
        )

    version_dir = storage.uploads_root / "workspace-1" / "version-1"
    assert list(version_dir.iterdir()) == []


def test_version_deletion_removes_raw_and_derived_files_with_readback(
    storage: FileStorage,
) -> None:
    storage.store_upload("workspace-1", "version-1", b"%PDF")
    storage.store_artifact("workspace-1", "version-1", "page-1", b"image")

    report = storage.delete_version("workspace-1", "version-1")

    assert report.verified
    assert storage.verify_version_absent("workspace-1", "version-1").verified


def test_incomplete_deletion_fails_closed(
    storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage.store_upload("workspace-1", "version-1", b"%PDF")
    monkeypatch.setattr(storage, "_remove_tree", lambda _path: None)

    with pytest.raises(StorageDeletionError, match="remain"):
        storage.delete_version("workspace-1", "version-1")
