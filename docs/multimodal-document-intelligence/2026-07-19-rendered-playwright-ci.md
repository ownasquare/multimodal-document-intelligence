# Rendered Playwright CI completion record

Date: 2026-07-19
Repository: `ownasquare/multimodal-document-intelligence`
Branch: `main`

## Outcome

The contributor gate now exercises the real API, worker, and Streamlit UI in Chromium instead of
stopping at an image build. The provider-free journey starts from empty state, prepares the bundled
Northstar sample, asks a cross-modal reconciliation question, validates chart and table-row
evidence, inspects Documents and Privacy, and repeats the visible contract at desktop and phone
sizes.

The test topology is isolated from the normal local workspace. It has a separate Compose project,
image, ports, network, and disposable volume, and it explicitly forces deterministic providers
with an empty OpenAI key.

## Changed surfaces

- `compose.e2e.yaml`: isolated, provider-free E2E topology on API port 18014 and UI port 18514.
- `tests/e2e/test_document_workspace.py`: rendered desktop and 390×844 phone journey.
- `.github/workflows/ci.yml`: production-shaped startup, Chromium run, failure diagnostics,
  failure-only artifact upload, and exact-project cleanup.
- `tests/contract/test_repository_hygiene.py`: repository contracts for isolation, current action
  runtimes, artifact policy, and cleanup safety.
- `Makefile`: deterministic source checks explicitly exclude rendered E2E.
- `docs/validation.md` and `CONTRIBUTING.md`: concise reproduction and contributor guidance.

## Validation evidence

### Validation environment

- macOS arm64 host, Python 3.12.8, Docker Compose 5.3.1.
- Existing normal stack remained healthy on `127.0.0.1:8014` and `127.0.0.1:8514`.
- Isolated test stack used `document-intelligence-e2e`, ports 18014/18514, image
  `document-intelligence:e2e`, and volume `document-intelligence-e2e_document-data`.

### Validation scope

- Focused repository contract: `5 passed`.
- Final strengthened Playwright journey from a freshly recreated E2E volume:
  `1 passed in 8.32s`.
- Complete deterministic gate: `125 passed, 4 deselected`, 81.14% branch coverage.
- Ruff lint/format, strict mypy, Bandit, pip-audit, wheel, and source distribution passed.
- Desktop viewport: 1440×1000.
- Phone viewport: 390×844, including horizontal-overflow assertions.
- Public-tree checker passed.
- Base and E2E Compose configuration validation passed.
- `git diff --check` passed.
- Fresh in-app Browser proof on the isolated UI confirmed the answer, page-3 Chart and page-2 Table
  row citations, Documents (Ready, 8 pages, 90 evidence items), Privacy, desktop rendering,
  390×844 rendering, equal phone client/scroll widths, and zero current console warnings/errors.

### Data integrity classification

The E2E flow uses only the fictional, redistribution-safe eight-page Northstar Q2 Operations
Review bundled with the repository. It does not use personal, customer, production, or confidential
documents.

### Mock and fixture usage

The browser, API, worker, SQLite/file state, parser, OCR, retrieval, and Streamlit path are real
local containers. Answer and embedding providers are intentionally deterministic. This is
synthetic-fixture, provider-free acceptance proof, not live-model accuracy proof.

### Production validation status

No hosted-development, staging, production, multi-user, or live-provider environment was used.
Public GitHub Actions proof is pending the validated commit and push.

### Localhost validation integrity

The rendered flow used the production-shaped Compose services and real local sockets. Its separate
project and disposable volume were verified, and the normal local project stayed healthy throughout.
Local proof is not labeled as hosted or production proof.

### Failure artifact behavior

A deliberate development failure retained `trace.zip` and a full-page failure screenshot under
`test-results/`. After the corrected test passed, `test-results/` did not exist. GitHub uploads
that folder plus Compose diagnostics only when the rendered job fails.

## Warning and issue triage

- `fixed_now`: pytest-playwright already supplied `base_url`; the duplicate context argument was
  removed.
- `fixed_now`: Streamlit joins metric label/value DOM text without a space; the metric assertion
  now permits either representation without weakening the required value.
- `fixed_now`: an initial YAML edit flattened the rendered pytest command with literal `+`
  tokens; the command now uses a parsed folded scalar and the repository contract rejects that
  failure shape.
- `not_suppressed`: no console issue filter, test warning suppression, retry loop, or relaxed
  evidence assertion was added.

Warning Suppression Status: `not_suppressed`.

## Commit and push evidence

Implementation commit: pending final local gate.
Push evidence: pending final local gate.
Public Actions run: pending push.

## Remaining boundaries

- Live OpenAI acceptance still requires explicit provider and cost authorization.
- Broad arbitrary-document accuracy still requires a licensed heterogeneous evaluation corpus.
- Shared hosting still requires identity, tenant isolation, and external durable infrastructure.
