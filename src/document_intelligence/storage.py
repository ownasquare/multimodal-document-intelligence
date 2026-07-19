"""Private content-addressed storage with fail-closed path handling."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")
_SUFFIX_PATTERN = re.compile(r"^\.[A-Za-z0-9]{1,12}$")
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]+")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_MIME_SUFFIXES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "application/json": ".json",
    "text/plain": ".txt",
}


class UnsafeStoragePathError(ValueError):
    """Raised when a persisted or generated key could escape managed storage."""


class StorageIntegrityError(RuntimeError):
    """Raised when bytes do not match their expected content identity."""


class StorageDeletionError(RuntimeError):
    """Raised when managed artifacts remain after a deletion attempt."""


@dataclass(frozen=True, slots=True)
class StoredFile:
    """Internal metadata for one atomically promoted managed file."""

    key: str
    sha256: str
    byte_size: int
    path: Path


@dataclass(frozen=True, slots=True)
class DeletionReport:
    """Readback proof for one version's raw and derived artifact removal."""

    workspace_id: str
    version_id: str
    uploads_absent: bool
    artifacts_absent: bool

    @property
    def verified(self) -> bool:
        return self.uploads_absent and self.artifacts_absent


def sanitize_display_name(value: str, *, fallback: str = "document.pdf") -> str:
    """Return a harmless display label; it is never used as a storage path."""

    normalized = unicodedata.normalize("NFKC", value).replace("\\", "/")
    normalized = normalized.rsplit("/", maxsplit=1)[-1]
    normalized = _CONTROL_PATTERN.sub("", normalized)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized).strip(" .")
    if normalized in {"", ".", ".."}:
        normalized = fallback
    if len(normalized) > 240:
        path = Path(normalized)
        suffix = path.suffix[:13]
        stem_limit = 240 - len(suffix)
        normalized = f"{path.stem[:stem_limit]}{suffix}".strip(" .") or fallback
    return normalized


class FileStorage:
    """Own raw uploads and derived assets beneath two private roots."""

    def __init__(self, uploads_root: Path, artifacts_root: Path) -> None:
        self.uploads_root = Path(uploads_root)
        self.artifacts_root = Path(artifacts_root)

    def initialize(self) -> None:
        for root in (self.uploads_root, self.artifacts_root):
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if root.is_symlink():
                raise UnsafeStoragePathError("managed storage root must not be a symbolic link")

    def store_upload(
        self,
        workspace_id: str,
        version_id: str,
        data: bytes | bytearray | memoryview | BinaryIO | Iterable[bytes],
        *,
        mime_type: str = "application/pdf",
        expected_sha256: str | None = None,
        max_bytes: int | None = None,
    ) -> StoredFile:
        """Atomically store an upload under server-owned workspace/version keys."""

        suffix = _MIME_SUFFIXES.get(mime_type)
        if suffix is None:
            raise ValueError("unsupported managed upload MIME type")
        return self._store(
            root=self.uploads_root,
            directory_parts=(
                self._identifier(workspace_id, "workspace_id"),
                self._identifier(version_id, "version_id"),
            ),
            data=data,
            suffix=suffix,
            expected_sha256=expected_sha256,
            max_bytes=max_bytes,
        )

    def store_artifact(
        self,
        workspace_id: str,
        version_id: str,
        artifact_id: str,
        data: bytes | bytearray | memoryview | BinaryIO | Iterable[bytes],
        *,
        suffix: str = ".png",
        expected_sha256: str | None = None,
        max_bytes: int | None = None,
    ) -> StoredFile:
        """Atomically store a content-addressed derived page, crop, or record."""

        if _ARTIFACT_ID_PATTERN.fullmatch(artifact_id) is None:
            raise ValueError("artifact_id is not safe for managed storage")
        normalized_suffix = suffix.lower()
        if _SUFFIX_PATTERN.fullmatch(normalized_suffix) is None:
            raise ValueError("artifact suffix is not safe for managed storage")
        return self._store(
            root=self.artifacts_root,
            directory_parts=(
                self._identifier(workspace_id, "workspace_id"),
                self._identifier(version_id, "version_id"),
                artifact_id,
            ),
            data=data,
            suffix=normalized_suffix,
            expected_sha256=expected_sha256,
            max_bytes=max_bytes,
        )

    def resolve_upload_key(self, key: str, *, require_exists: bool = True) -> Path:
        """Resolve an exact raw-file key and reject traversal or symlink aliases."""

        parts = self._validated_key_parts(key, expected_count=3)
        self._identifier(parts[0], "workspace_id")
        self._identifier(parts[1], "version_id")
        self._validate_digest_filename(parts[2])
        return self._resolve(self.uploads_root, parts, require_exists=require_exists)

    def resolve_artifact_key(self, key: str, *, require_exists: bool = True) -> Path:
        """Resolve an exact derived-file key and reject traversal or symlinks."""

        parts = self._validated_key_parts(key, expected_count=4)
        self._identifier(parts[0], "workspace_id")
        self._identifier(parts[1], "version_id")
        if _ARTIFACT_ID_PATTERN.fullmatch(parts[2]) is None:
            raise UnsafeStoragePathError("artifact key contains an invalid artifact identifier")
        self._validate_digest_filename(parts[3])
        return self._resolve(self.artifacts_root, parts, require_exists=require_exists)

    def read_upload(self, key: str) -> bytes:
        path = self.resolve_upload_key(key)
        return path.read_bytes()

    def read_artifact(self, key: str) -> bytes:
        path = self.resolve_artifact_key(key)
        return path.read_bytes()

    def upload_exists(self, key: str) -> bool:
        try:
            return self.resolve_upload_key(key, require_exists=False).is_file()
        except UnsafeStoragePathError:
            return False

    def artifact_exists(self, key: str) -> bool:
        try:
            return self.resolve_artifact_key(key, require_exists=False).is_file()
        except UnsafeStoragePathError:
            return False

    def delete_version(self, workspace_id: str, version_id: str) -> DeletionReport:
        """Remove all managed bytes for one version, then verify both roots."""

        workspace = self._identifier(workspace_id, "workspace_id")
        version = self._identifier(version_id, "version_id")
        for root in (self.uploads_root, self.artifacts_root):
            target = self._resolve(root, (workspace, version), require_exists=False)
            if target.exists() or target.is_symlink():
                self._remove_tree(target)
        return self.verify_version_absent(workspace, version)

    def delete_document(
        self, workspace_id: str, version_ids: Sequence[str]
    ) -> list[DeletionReport]:
        """Delete every explicitly enumerated immutable version."""

        reports = [self.delete_version(workspace_id, version_id) for version_id in version_ids]
        if not all(report.verified for report in reports):
            raise StorageDeletionError("one or more document versions still have managed files")
        return reports

    def verify_version_absent(self, workspace_id: str, version_id: str) -> DeletionReport:
        workspace = self._identifier(workspace_id, "workspace_id")
        version = self._identifier(version_id, "version_id")
        upload_path = self._resolve(self.uploads_root, (workspace, version), require_exists=False)
        artifact_path = self._resolve(
            self.artifacts_root, (workspace, version), require_exists=False
        )
        report = DeletionReport(
            workspace_id=workspace,
            version_id=version,
            uploads_absent=not upload_path.exists() and not upload_path.is_symlink(),
            artifacts_absent=not artifact_path.exists() and not artifact_path.is_symlink(),
        )
        if not report.verified:
            raise StorageDeletionError("managed files remain after version deletion")
        return report

    def _store(
        self,
        *,
        root: Path,
        directory_parts: tuple[str, ...],
        data: bytes | bytearray | memoryview | BinaryIO | Iterable[bytes],
        suffix: str,
        expected_sha256: str | None,
        max_bytes: int | None,
    ) -> StoredFile:
        self.initialize()
        expected = expected_sha256.lower() if expected_sha256 is not None else None
        if expected is not None and _DIGEST_PATTERN.fullmatch(expected) is None:
            raise ValueError("expected_sha256 must be a lowercase SHA-256 digest")
        if max_bytes is not None and max_bytes < 1:
            raise ValueError("max_bytes must be positive")

        directory = self._resolve(root, directory_parts, require_exists=False)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory = self._resolve(root, directory_parts, require_exists=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".incoming-", dir=directory)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        byte_size = 0
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                for chunk in self._chunks(data):
                    if not chunk:
                        continue
                    byte_size += len(chunk)
                    if max_bytes is not None and byte_size > max_bytes:
                        raise StorageIntegrityError(
                            "managed file exceeds its configured byte limit"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if byte_size == 0:
                raise StorageIntegrityError("managed files must not be empty")
            hexdigest = digest.hexdigest()
            if expected is not None and hexdigest != expected:
                raise StorageIntegrityError("managed file does not match its expected SHA-256")
            filename = f"{hexdigest}{suffix}"
            destination = self._resolve(root, (*directory_parts, filename), require_exists=False)
            if destination.exists():
                existing = self._hash_file(destination)
                if existing != (hexdigest, byte_size):
                    raise StorageIntegrityError(
                        "content-addressed destination contains other bytes"
                    )
                temporary.unlink()
            else:
                os.replace(temporary, destination)
                os.chmod(destination, 0o600)
                self._fsync_directory(directory)
            key = PurePosixPath(*directory_parts, filename).as_posix()
            return StoredFile(key=key, sha256=hexdigest, byte_size=byte_size, path=destination)
        finally:
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()

    @staticmethod
    def _chunks(
        data: bytes | bytearray | memoryview | BinaryIO | Iterable[bytes],
    ) -> Iterator[bytes]:
        if isinstance(data, (bytes, bytearray, memoryview)):
            yield bytes(data)
            return
        read = getattr(data, "read", None)
        if callable(read):
            while True:
                chunk = read(1024 * 1024)
                if not chunk:
                    return
                if not isinstance(chunk, bytes):
                    raise TypeError("binary stream returned a non-bytes chunk")
                yield chunk
        else:
            for chunk in data:
                if not isinstance(chunk, bytes):
                    raise TypeError("managed file iterables must yield bytes")
                yield chunk

    def _resolve(
        self,
        root: Path,
        parts: Sequence[str],
        *,
        require_exists: bool,
    ) -> Path:
        self.initialize()
        root_path = root.resolve(strict=True)
        if root.is_symlink():
            raise UnsafeStoragePathError("managed storage root must not be a symbolic link")
        candidate = root_path.joinpath(*parts)
        cursor = root_path
        for part in parts:
            if part in {"", ".", ".."} or "/" in part or "\\" in part:
                raise UnsafeStoragePathError("managed storage key contains an unsafe component")
            cursor = cursor / part
            if cursor.is_symlink():
                raise UnsafeStoragePathError("managed storage key traverses a symbolic link")
        resolved = candidate.resolve(strict=require_exists)
        try:
            resolved.relative_to(root_path)
        except ValueError as exc:
            raise UnsafeStoragePathError("managed storage key escapes its configured root") from exc
        return resolved

    @staticmethod
    def _validated_key_parts(key: str, *, expected_count: int) -> tuple[str, ...]:
        if not key or "\\" in key or key.startswith("/"):
            raise UnsafeStoragePathError("managed storage key is not a relative POSIX key")
        parsed = PurePosixPath(key)
        parts = parsed.parts
        if (
            len(parts) != expected_count
            or any(part in {"", ".", ".."} for part in parts)
            or parsed.as_posix() != key
        ):
            raise UnsafeStoragePathError("managed storage key has an invalid shape")
        return tuple(parts)

    @staticmethod
    def _identifier(value: str, field: str) -> str:
        if _IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise ValueError(f"{field} is not a safe managed-storage identifier")
        return value

    @staticmethod
    def _validate_digest_filename(filename: str) -> None:
        path = Path(filename)
        if (
            _DIGEST_PATTERN.fullmatch(path.stem) is None
            or _SUFFIX_PATTERN.fullmatch(path.suffix) is None
        ):
            raise UnsafeStoragePathError("managed filename is not content addressed")

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        byte_size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                byte_size += len(chunk)
                digest.update(chunk)
        return digest.hexdigest(), byte_size

    @classmethod
    def _remove_tree(cls, path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return
        if not path.exists():
            return
        with os.scandir(path) as entries:
            for entry in entries:
                entry_path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    cls._remove_tree(entry_path)
                else:
                    entry_path.unlink()
        path.rmdir()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
