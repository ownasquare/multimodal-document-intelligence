# Security and privacy

## Threat model

Documents, filenames, metadata, extracted passages, visual labels, OCR text, provider output, and
HTTP inputs are untrusted. The application is designed for one user on one trusted host. It does
not claim tenant isolation or safe direct exposure to the public internet.

## Controls

- API and UI bind to loopback by default; non-loopback API binding requires a token.
- Upload count, byte size, PDF signature, page count, text lengths, and retrieval bounds are
  server-enforced.
- Original filenames are sanitized display values. Storage uses generated IDs and verifies every
  resolved path remains under the configured private root.
- PDF JavaScript, launch actions, attachments, macros, and embedded instructions are never run.
- OCR and page rendering are bounded by page and timeout settings.
- Retrieved source text is delimited as evidence and cannot change prompts, scope, provider
  settings, or tool access.
- Provider calls receive no tools. Returned citation IDs are checked against the authoritative
  retrieved set.
- Provider keys and application tokens remain `SecretStr` server settings and are absent from API
  schemas and browser code.
- Errors use stable codes and sanitized messages. Normal logs contain IDs, counts, stages, and
  timings rather than full passages or secret values.
- Deletion reports success only after metadata, vector, raw-file, render, and crop readback.
- Containers run non-root, drop capabilities, prevent privilege escalation, and use read-only root
  filesystems.

## Chroma dependency hardening

The runtime intentionally pins `chromadb==0.6.3`, outside the affected `>=1.0.0,<=1.5.9` range
reported by [PYSEC-2026-311](https://osv.dev/vulnerability/PYSEC-2026-311) and
[CVE-2026-45829](https://nvd.nist.gov/vuln/detail/CVE-2026-45829). The affected Chroma releases
expose a pre-authentication code-execution path through model-loading behavior.

This application uses only the embedded `PersistentClient`; it does not start or expose a Chroma
HTTP service. Embeddings are supplied explicitly by the application, anonymized telemetry is
disabled, and a project-owned no-op telemetry client keeps product events local. Do not raise the
Chroma version until the selected release is outside the advisory range and the dependency audit,
persistent-index compatibility tests, ingestion/query/deletion lifecycle, and telemetry assertions
all pass.

## External-provider disclosure

Deterministic mode does not call an external model. OpenAI mode may send only the bounded retrieved
excerpts and selected page/crop images needed for a question or visual description. Full PDFs are
not persisted in a provider file store by the application. Review the provider's current data
controls and your organization's policy before using sensitive material.

## Release checks

`make security` runs Bandit and pip-audit. `scripts/check_public_repo.py` fails on private
environment filenames, runtime-data directories, and obvious non-placeholder credential
assignments. CI never requires provider credentials and live tests are opt-in.
