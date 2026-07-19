# Architecture

## Product boundary

Document Intelligence is a production-style, single-user, single-host workspace. FastAPI is the
only system-of-record interface. A separate leased worker prepares documents. Streamlit is a thin
HTTP client. SQLite, Chroma, and the private artifact filesystem are not exposed to browser code.

This boundary is honest about its limits: multi-user or multi-host use requires PostgreSQL,
private object storage, a production queue, distributed locks, tenant authorization, TLS, and an
identity-aware proxy.

## State ownership

- **SQLite WAL** owns workspaces, document/version lifecycle, durable jobs, element metadata,
  conversations, answers, claims, citation snapshots, and active profile fingerprints.
- **Private filesystem state** owns original PDFs, rendered pages, and evidence crops. User
  filenames are display metadata, never storage paths.
- **Chroma** owns searchable vectors only. It cannot make a failed, stale, deleting, or deleted
  document visible.
- **Streamlit session state** owns navigation and unsubmitted drafts only. A refresh reads durable
  documents, jobs, conversations, answers, and citations from FastAPI.

## Upload and ingestion

1. FastAPI enforces the batch count, streams a bounded upload, validates the PDF signature, hashes
   bytes, and writes a UUID-addressed staging file beneath the private upload root.
2. Accepted metadata, immutable version, optional idempotency record, and queued job commit in one
   SQLite transaction before the API returns `202`.
3. The worker atomically leases work and records human-readable progress through reading, text,
   tables, OCR, visual understanding, indexing, and verification.
4. pdfplumber extracts positioned words, layout objects, image bounds, and tables. pypdfium2
   renders pages/crops. OCR runs only for low-text regions when Tesseract is available.
5. Table summaries and rows, native text, OCR, captions, page summaries, and visual descriptions
   become stable LlamaIndex nodes with document/version/page/modality metadata.
6. The worker embeds and idempotently upserts nodes into profile- and modality-compatible Chroma
   collections.
7. SQLite marks a version ready only after expected-versus-observed element/vector readback passes.
   Partial extraction becomes `ready_with_warnings`; it is never silent success.

Provider calls and parsing never hold a SQLite write transaction. A crashed worker leaves a lease
that can expire and be reclaimed with bounded attempts. Retry removes partial records for the exact
version before rebuilding them.

## Evidence model

Each element retains:

- workspace, document, and immutable version IDs;
- one-based page number;
- modality;
- normalized page bounding box where available;
- extracted text or structured table representation;
- derived asset key for page/crop evidence;
- extraction method and confidence; and
- bounded metadata such as headers, units, captions, and warnings.

Original PDFs and page images stay outside Chroma. Chroma metadata stores only routing/provenance
fields and bounded searchable representations.

## Retrieval

1. The query service snapshots the selected ready document versions from SQLite.
2. A transparent planner detects numeric/table, trend/chart, diagram/process, broad-summary, or
   exact-language signals.
3. The deterministic or OpenAI embedding provider embeds the query with the active profile.
4. Chroma semantic results and normalized lexical results are fused with reciprocal-rank fusion.
5. Modality weights favor table rows for numeric comparisons, visuals for charts/legends/processes,
   and page summaries for broad questions.
6. Results are diversified across pages and modalities while preserving the explicit document
   scope.
7. Immediately before returning evidence, the service rechecks that every version remains ready.

Changing an embedding model, vector dimensions, parser profile, node serializer, modality, or
distance metric changes the compatibility fingerprint and requires a new collection/reindex.

## Multimodal answers and citations

The answer provider receives bounded untrusted evidence delimiters, nearby text/table context, and
only the most relevant page/crop images. It receives no tools and cannot alter document scope or
provider settings. OpenAI response storage is disabled where supported.

The provider returns structured claims and selected evidence IDs. The server rejects unknown,
out-of-scope, stale, or malformed IDs and builds citations from authoritative element metadata.
Invalid support fails closed to an explicit abstention. A completed answer, claims, document-version
snapshot, and normalized citations commit together.

## Deletion

Deletion is a durable job. SQLite first changes the document to `deleting`, immediately excluding
it from retrieval. The worker removes exact Chroma records, raw upload bytes, page renders, crops,
and active elements, then verifies zero readback before marking `deleted`. Saved answers retain
textual citation tombstones, but deleted assets are unavailable.

## Scaling path

Before adding API replicas, multiple hosts, or users, replace SQLite with PostgreSQL, move files to
private object storage, run Chroma as an authenticated service or select another production vector
database, use an external durable queue, introduce distributed leases, and apply owner/tenant
filters to every row, vector, file, and cache key.

