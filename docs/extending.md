# Extending Document Intelligence

Extensions stay behind small project-owned contracts. The API, worker, UI, persistence, and model
SDKs should not reach through one another.

## Pick the smallest extension point

| You want to add | Contract or seam | Implement beside | Wire it in | Focused proof |
|---|---|---|---|---|
| A PDF parser | `DocumentParser` | `parsers/` | `create_runtime()` in `container.py` | `tests/unit/test_pdf_parser.py`, ingestion integration test |
| An embedding service | `EmbeddingProvider` | `providers/` | `_embedding_provider()` | `tests/unit/test_providers.py`, `test_vector_index.py` |
| Visual descriptions | `VisualUnderstandingProvider` | `providers/` | `_visual_provider()` | provider unit test, multimodal ingestion test |
| Answer generation | `AnswerProvider` | `providers/` | `_answer_provider()` | provider, citation, and answer tests |
| A vector backend | `VectorIndex` | `retrieval/` | `create_runtime()` | version isolation, delete readback, retrieval quality |
| Ranking behavior | `RetrievalPlanner` / reranker functions | `retrieval/planner.py`, `retrieval/reranker.py` | `HybridRetriever` | planner unit test and golden eval |
| A visible workflow | `UiClient` and API contracts | `ui/` and `api/routes/` | `ui/app.py`, `api/app.py` | Streamlit AppTest plus Playwright/browser proof |

Search for the named class before coding; the table uses source-relative names so it remains useful
in forks and packaged checkouts.

## Provider walkthrough

The same sequence applies to a new embedding, visual-understanding, or answer provider.

1. Implement the matching protocol in `src/document_intelligence/providers/base.py`.
2. Return only the project models defined there; keep SDK response objects inside your adapter.
3. Give the adapter a stable `profile` containing provider, model, and semantic version identity.
4. Add explicit timeouts, bounded transient retries, and `ProviderError` failures with sanitized
   messages and stable codes.
5. Add a deterministic fake and unit tests under `tests/unit/test_providers.py`.
6. Add a literal configuration choice in `config.py` and wire it through the matching helper in
   `container.py`.
7. Add an explicitly opted-in smoke test under `tests/live/`; deterministic tests must never call
   the network.
8. Run `uv run pytest -q tests/unit/test_providers.py` and then `make check`.

An answer provider receives only bounded `ProviderEvidence` records selected by the server. It must
return claims with allowed evidence IDs. Citation document names, page numbers, modalities, and
asset URLs always come from durable server records, never generated prose.

## Parser rules

A parser returns `ParsedDocument`, `ParsedPage`, and `ParsedElement` values from `parsers/base.py`.
Preserve:

- one-based page numbers;
- normalized bounding boxes and source units;
- extraction method, confidence, asset provenance, and safe warnings;
- page-local failure when the remainder of the document is still usable; and
- `content_trust="untrusted"` for all document-derived content.

A semantic parser change must update `PARSER_SCHEMA_VERSION` in `container.py`. That creates a new
profile fingerprint and prevents old and new vectors from mixing.

## Embedding and vector rules

Embeddings are explicit: a vector store must never invoke its own hidden embedding model. Keep
text/table and visual modality groups in compatible but separate collections. A profile change must
produce a new collection fingerprint.

A vector backend must prove idempotent upsert, workspace and document-version scoping, exact-version
delete, count/readback, and exclusion of failed, deleting, deleted, stale, or incompatible versions.
SQLite remains lifecycle authority even when the retrieval backend changes.

## Retrieval and citation rules

New ranking behavior belongs in the planner, reranker, or `HybridRetriever`; it must not widen the
user-selected document scope. Add a golden question when the change is intended to improve a
specific lookup, numeric, multimodal, conflict, or abstention behavior.

Every material answer claim needs valid evidence. If support is missing, fail closed or abstain.
Stored answers keep immutable evidence snapshots so later document deletion does not rewrite history.

## UI rules

Keep **Ask** and **Documents** primary. Evidence browsing, preparation, privacy, provider details,
traces, parser controls, and vector internals use progressive disclosure. Prefer short labels and
contextual `(i)` help over paragraphs of instructions.

Visible changes require Streamlit AppTest coverage and rendered desktop/mobile browser proof.
Playwright is the E2E framework; this project does not use Cypress.

## Before a pull request

```bash
uv run pytest -q path/to/focused_test.py
make check
```

Explain the user-visible behavior, changed boundary, profile or migration impact, deterministic
proof, and any live-provider, container, browser, hosted, or production proof not performed.
