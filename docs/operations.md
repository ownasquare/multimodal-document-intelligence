# Operations

## Local processes

The normal local topology is one FastAPI process, one leased worker, and one Streamlit process.
Run `make demo` for the supervised credential-free workflow or start `make api`, `make worker`, and
`make ui` independently.

Liveness proves only that the API process can respond. Readiness verifies the default workspace
record and required upload, artifact, and Chroma directories; in demo mode it also verifies that
the bundled sample exists. OCR absence is reported as a warning. Readiness does not make a paid
provider call and does not currently prove worker freshness or execute a vector query. Document
readiness remains a separate per-version state.

## Container topology

Compose runs separate API, leased-worker, and Streamlit containers from one image. Ports 8014 and
8514 are published to host loopback only. The API binds to `0.0.0.0` inside the private Compose
network and therefore requires the application token supplied to all three services.

Use `docker compose up --build -d`, wait for `docker compose ps` to show healthy API and UI
services, then run:

```bash
docker compose exec -T api document-intelligence doctor
```

The image includes Tesseract English data, so container OCR should report ready. `docker compose
down` preserves the named data volume. `docker compose down -v` permanently removes local
documents, metadata, vectors, and evidence; use it only with explicit deletion intent or after a
verified backup.

## Data layout

Host-process mode uses the default `.data/` root. Compose uses `/data`, backed by the private
`document-data` named volume. The selected root contains:

- `document-intelligence.sqlite3` and SQLite WAL files;
- `uploads/<workspace>/<version>/source.pdf`;
- `artifacts/<workspace>/<version>/pages/` and visual crops; and
- `chroma/` persistent vector collections.

The directory is ignored by git and should have owner-only permissions. Do not place it in a
public sync folder.

## Backup

Stop the worker or otherwise ensure no ingestion/deletion lease is active. Use SQLite's backup API
or a filesystem snapshot that includes the database, uploads, artifacts, and Chroma directories at
one consistent point. Encrypt backups containing real documents. Record the application version
and active parser/embedding profile with the snapshot.

## Restore

Restore the complete data root to a private empty location, verify owner-only permissions, run
`document-intelligence doctor`, start the API and worker, and check readiness plus document/vector
inventory before serving the UI. A database-only restore is incomplete because citations and
vectors may reference missing assets.

## Reindex and profile changes

Never change embedding dimensions or parser/node semantics in place. Configure the new profile,
queue reprocessing, verify the new collection and version, and only then remove the incompatible
index through the documented deletion path.

## Troubleshooting

- **OCR unavailable:** install Tesseract 5 and the required language packs; born-digital content
  remains usable and affected scans show warnings.
- **Document stays queued:** confirm one worker is running and its heartbeat is current.
- **Job needs attention:** open Activity for the safe error, resolve the cause, then use Retry.
- **UI cannot connect:** confirm FastAPI is healthy at `http://127.0.0.1:8014/health/ready`.
- **Provider mode fails at startup:** verify the server has a configured key and model names; do not
  paste the key into logs or issue reports.
- **Profile mismatch:** reprocess into a new compatible collection rather than editing metadata.
