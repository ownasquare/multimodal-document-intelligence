"""Bounded upload, inventory, lifecycle, and element routes."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Sequence
from contextlib import ExitStack
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from document_intelligence.api.contracts import (
    ApplicationServices,
    PageEnvelope,
    UploadInput,
)
from document_intelligence.api.dependencies import get_api_settings, get_services
from document_intelligence.api.errors import ApiError
from document_intelligence.config import Settings
from document_intelligence.models import (
    ContentElement,
    Document,
    DocumentStatus,
    IngestionJob,
    Modality,
    UploadReceipt,
)
from document_intelligence.storage import sanitize_display_name

router = APIRouter(prefix="/documents", tags=["documents"])
_PDF_CONTENT_TYPES = frozenset({"application/pdf", "application/octet-stream"})
_READ_SIZE = 1024 * 1024


async def _stage_upload(upload: UploadFile, settings: Settings) -> UploadInput:
    content_type = (
        (upload.content_type or "application/octet-stream").split(";", maxsplit=1)[0].lower()
    )
    if content_type not in _PDF_CONTENT_TYPES:
        await upload.close()
        raise ApiError(
            "invalid_file_type",
            "Only PDF documents can be uploaded.",
            status_code=415,
        )
    digest = hashlib.sha256()
    byte_size = 0
    leading = b""
    try:
        with ExitStack() as stack:
            stream = stack.enter_context(
                tempfile.SpooledTemporaryFile(
                    max_size=min(settings.max_file_bytes, 2 * 1024 * 1024), mode="w+b"
                )
            )
            while chunk := await upload.read(_READ_SIZE):
                byte_size += len(chunk)
                if byte_size > settings.max_file_bytes:
                    raise ApiError(
                        "file_too_large",
                        "A document exceeds the configured upload limit.",
                        status_code=413,
                    )
                if len(leading) < 5:
                    leading += chunk[: 5 - len(leading)]
                digest.update(chunk)
                stream.write(chunk)
            if byte_size == 0 or not leading.startswith(b"%PDF-"):
                raise ApiError(
                    "invalid_pdf",
                    "This file is not a valid PDF upload.",
                    status_code=422,
                )
            stream.seek(0)
            result = UploadInput(
                display_name=sanitize_display_name(upload.filename or "document.pdf"),
                content_type="application/pdf",
                byte_size=byte_size,
                sha256=digest.hexdigest(),
                stream=stream,
            )
            stack.pop_all()
            return result
    finally:
        await upload.close()


def _close_uploads(uploads: Sequence[UploadInput]) -> None:
    for upload in uploads:
        upload.stream.close()


@router.post("", response_model=list[UploadReceipt], status_code=status.HTTP_202_ACCEPTED)
async def upload_documents(
    files: Annotated[list[UploadFile], File(...)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=200),
    ],
    services: Annotated[ApplicationServices, Depends(get_services)],
    settings: Annotated[Settings, Depends(get_api_settings)],
) -> Sequence[UploadReceipt]:
    if not files:
        raise ApiError(
            "empty_upload",
            "Choose at least one PDF to upload.",
            status_code=422,
        )
    if len(files) > settings.max_upload_batch:
        for upload in files:
            await upload.close()
        raise ApiError(
            "upload_batch_too_large",
            "Too many documents were included in one upload.",
            status_code=413,
        )
    staged: list[UploadInput] = []
    try:
        for upload in files:
            staged.append(await _stage_upload(upload, settings))
        return await run_in_threadpool(
            services.accept_uploads,
            tuple(staged),
            idempotency_key=idempotency_key,
        )
    finally:
        _close_uploads(staged)


@router.get("", response_model=PageEnvelope[Document])
def list_documents(
    services: Annotated[ApplicationServices, Depends(get_services)],
    query: Annotated[str | None, Query(max_length=240)] = None,
    document_status: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    sort: Annotated[Literal["recent", "oldest", "name", "name_desc"], Query()] = "recent",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageEnvelope[Document]:
    page = services.list_documents(
        query=query,
        status=document_status,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return PageEnvelope[Document](
        items=list(page.items), total=page.total, limit=limit, offset=offset
    )


@router.get("/{document_id}", response_model=Document)
def get_document(
    document_id: str,
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> Document:
    document = services.get_document(document_id)
    if document is None:
        raise ApiError(
            "document_not_found",
            "The requested document was not found.",
            status_code=404,
        )
    return document


@router.get("/{document_id}/elements", response_model=PageEnvelope[ContentElement])
def list_elements(
    document_id: str,
    services: Annotated[ApplicationServices, Depends(get_services)],
    modality: Modality | None = None,
    page_number: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageEnvelope[ContentElement]:
    page = services.list_document_elements(
        document_id,
        modality=modality,
        page_number=page_number,
        limit=limit,
        offset=offset,
    )
    return PageEnvelope[ContentElement](
        items=list(page.items), total=page.total, limit=limit, offset=offset
    )


@router.post(
    "/{document_id}/reprocess",
    response_model=IngestionJob,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_document(
    document_id: str,
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> IngestionJob:
    return services.reprocess_document(document_id)


@router.delete(
    "/{document_id}",
    response_model=IngestionJob,
    status_code=status.HTTP_202_ACCEPTED,
)
def delete_document(
    document_id: str,
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> IngestionJob:
    return services.delete_document(document_id)
