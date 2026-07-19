# Local deployment

## Supported boundary

The supported deployment for release 0.1.0 is local, single-user, single-host Docker Compose.
Setting `DOCINTEL_ENVIRONMENT=production` enables stricter runtime defaults; it is not evidence of
a hosted or production deployment. Public, multi-tenant, and multi-host operation are unsupported.

## Start and verify

1. Replace the example `DOCINTEL_API_TOKEN` before sharing access beyond the current machine.
2. Run `docker compose up --build -d`.
3. Wait for `docker compose ps` to show healthy API and UI services and a running worker.
4. Run `docker compose exec -T api document-intelligence doctor`.
5. Open [http://127.0.0.1:8514](http://127.0.0.1:8514), create the sample workspace, wait for the
   document to become ready, ask a sample question, and open one citation asset.

The published ports bind to host loopback. Inside the private Compose network, the API binds to
`0.0.0.0` and is protected by the same application token supplied to the worker and Streamlit
server. The UI never receives the token or a provider key in browser code.

## Persistence and recovery

Application state lives in the private `document-data` named volume at `/data`: SQLite metadata,
raw PDFs, derived evidence, and embedded Chroma indexes must be backed up and restored together.
Use `docker compose down` for a normal shutdown; it preserves the volume. `docker compose down -v`
irreversibly removes the local workspace and must be used only with explicit deletion intent or
after a verified backup. See [Operations](operations.md) for consistent backup and restore steps.

## OpenAI mode

Provide `DOCINTEL_OPENAI_API_KEY` to the server-side Compose environment only, and explicitly set
both provider selectors when required. Do not place a key in browser fields, logs, screenshots, or
committed Compose files. Live-provider acceptance is separate from deterministic/container proof
and must record the model, account boundary, date, and result without recording the credential.

## Chroma boundary

Chroma is embedded and never exposed as an HTTP service. Release 0.1.0 pins `chromadb==0.6.3` for
the security reason described in [Security and privacy](security.md#chroma-dependency-hardening).
An upgrade requires a clean dependency audit plus persistent ingest, query, reprocess, deletion,
restart, and telemetry proof.

## Shutdown and inspection

Use `docker compose logs --tail=200 api worker ui` for bounded diagnostics and `docker compose ps`
for current process state. Do not include document content or credentials in shared logs. A local
Compose success does not imply hosted availability, TLS, identity-aware access, tenant isolation,
provider quota, backups, monitoring, or production acceptance.
