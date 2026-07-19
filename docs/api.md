# HTTP API

The API is versioned under `/api/v1`. JSON inputs use strict Pydantic schemas, bounded strings, and
stable sanitized errors. When configured, clients send `Authorization: Bearer <application-token>`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health/live` | Process liveness |
| `GET` | `/health/ready` | Local dependency and worker readiness |
| `GET` | `/api/v1/status` | Sanitized workspace/provider summary |
| `GET` | `/api/v1/documents` | Search, filter, sort, and page documents |
| `POST` | `/api/v1/documents` | Accept one bounded multipart PDF batch |
| `GET` | `/api/v1/documents/{id}` | Read one document |
| `GET` | `/api/v1/documents/{id}/elements` | Explore retained evidence metadata |
| `POST` | `/api/v1/documents/{id}/reprocess` | Queue a new compatible preparation job |
| `DELETE` | `/api/v1/documents/{id}` | Queue verified permanent removal |
| `GET` | `/api/v1/jobs` | List recent durable activity |
| `POST` | `/api/v1/jobs/{id}/retry` | Retry eligible failed work |
| `GET` | `/api/v1/conversations` | List saved question threads |
| `POST` | `/api/v1/query` | Retrieve, answer, validate citations, and persist |
| `GET` | `/api/v1/assets/{version}/{asset}` | Return an allowlisted citation preview |
| `POST` | `/api/v1/demo/sample` | Idempotently accept the synthetic sample in demo mode |

Upload returns `202` only after file storage plus document/version/job metadata are durable. Query
scope is an explicit list of ready document IDs; an empty list means all ready documents in the
current workspace, not an unbounded cross-workspace search. The response includes answer text,
claims, citations, modalities used, and abstention state.

Errors use the shape:

```json
{
  "detail": {
    "code": "invalid_pdf",
    "message": "This file is not a supported PDF.",
    "request_id": "..."
  }
}
```

Internal paths, provider exceptions, keys, tokens, and full source passages are excluded.

