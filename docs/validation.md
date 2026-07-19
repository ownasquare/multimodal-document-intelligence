# Validation and proof boundaries

## Deterministic release gate

The default gate is credential-free and network-disabled for tests:

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run bandit -q -r src scripts
uv run pytest -m "not live and not e2e" --disable-socket --allow-unix-socket \
  -W error --cov=document_intelligence --cov-branch --cov-report=term-missing -q
uv run pip-audit
uv build
uv run python scripts/check_public_repo.py
docker compose config --quiet
git diff --check
```

Tests use generated synthetic files, temporary SQLite/filesystem/Chroma state, deterministic
embeddings and answer providers, mocked OpenAI transports, and local API/UI fixtures. The HTTP
lifecycle in `tests/e2e/test_api_workflow.py` uses an in-process ASGI transport. Streamlit tests use
`AppTest` with a fake API client. Neither is classified as integrated rendered-browser proof.
Rendered E2E is kept outside this socket-disabled source gate because it needs Chromium and the
local Compose network. No personal document or paid provider request belongs in either gate.

## Rendered Playwright gate

Every push to `main` and every pull request runs `tests/e2e/test_document_workspace.py` against
the real API, worker, and Streamlit containers. The test uses a dedicated Compose project, image,
ports, network, and disposable volume:

- project: `document-intelligence-e2e`
- API: `127.0.0.1:18014`
- UI: `127.0.0.1:18514`
- image: `document-intelligence:e2e`
- volume: `document-intelligence-e2e_document-data`

`compose.e2e.yaml` requires Docker Compose 2.24.4 or newer for `!override`. It explicitly forces
deterministic answer and embedding providers and clears the OpenAI key, so ambient provider
configuration cannot turn this gate into a paid request.

To reproduce the CI journey locally:

```bash
uv sync --all-groups --frozen
uv run playwright install chromium
docker compose --project-name document-intelligence-e2e \
  -f compose.yaml -f compose.e2e.yaml \
  down --timeout 30 --volumes --remove-orphans
docker compose --project-name document-intelligence-e2e \
  -f compose.yaml -f compose.e2e.yaml \
  up --build --detach --wait --wait-timeout 300
uv run pytest -q -m e2e tests/e2e/test_document_workspace.py \
  --browser chromium \
  --base-url http://127.0.0.1:18514 \
  --tracing retain-on-failure \
  --screenshot only-on-failure \
  --full-page-screenshot \
  --output test-results
docker compose --project-name document-intelligence-e2e \
  -f compose.yaml -f compose.e2e.yaml \
  down --timeout 30 --volumes --remove-orphans
```

On Linux, use `uv run playwright install --with-deps chromium` so the required system libraries
are installed with Chromium. GitHub Actions uses that form.

The journey starts from an empty E2E volume, creates the bundled sample through onboarding,
confirms the sample endpoint is idempotent, waits for preparation, asks the reconciliation
question, checks page-3 Chart and page-2 Table row evidence, and inspects Documents and Privacy.
It repeats the visible answer and navigation contract at 1440×1000 and 390×844, checks phone
overflow, and fails on current-run console warnings, console errors, or page errors.

pytest-playwright closes every context. Traces and full-page screenshots are retained in
`test-results/` only on failure; GitHub uploads that folder together with Compose diagnostics
only for failed runs. A successful journey leaves no browser artifact directory. Always use the
exact E2E project name for cleanup—never remove the normal `document-intelligence` volume.

## Evaluation contract

The Northstar evaluation separately scores retrieval evidence, final facts, numeric units,
modality use, citation page/element accuracy, and abstention. A correct sentence with a wrong source
does not pass. Cross-modal questions must record the required modalities rather than passing from a
canned response.

## Proof layers

- **Source and deterministic tests:** reproducible without credentials.
- **Rendered browser proof:** automated Compose-backed Chromium behavior using the synthetic
  sample at desktop and phone sizes.
- **Local container proof:** image/topology/volume/health behavior on the current host.
- **OCR proof:** host-process OCR requires host Tesseract; the Compose image includes English
  Tesseract and is validated separately with `document-intelligence doctor` and an OCR-backed
  sample result.
- **Live-provider proof:** opt-in and limited to the configured account, model, quota, and request.
- **Hosted-development proof:** not implied by local success.
- **Production proof:** outside the current single-host boundary unless separately performed and
  recorded.

The [0.1.0 open-source readiness record](multimodal-document-intelligence/2026-07-18-open-source-adoption-readiness.md) records
exact results and remaining boundaries for the current release.

## 0.1.0 verified results — 2026-07-18

### Validation environment

- macOS arm64 host with Python 3.12.8 and Docker Desktop.
- Final local image:
  `sha256:38e5d1a9a19189be2e79b972a4fb114e8a851009a4ee052c76288c9c5da16736`.
- Compose services: API and UI healthy; worker running. Ports bind only to
  `127.0.0.1:8014` and `127.0.0.1:8514`.
- Provider credentials were explicitly unavailable to the deterministic gate. No paid or live
  provider request was made.

### Validation scope

- `123 passed, 3 deselected` with live-provider tests excluded and network sockets disabled.
- Branch coverage: `81.14%`, above the enforced 80% gate.
- Ruff format and lint: 89 files clean.
- mypy: 55 source files clean under strict configuration.
- Bandit: no findings. `pip-audit`: no known dependency vulnerabilities; the unpublished local
  package was the only skipped package.
- `uv lock --check`: 187 packages resolved. Wheel and source distribution both built.
- Public-tree check, Compose configuration, and `git diff --check` passed.
- The wheel contains
  `document_intelligence/resources/northstar-q2-operations-review.pdf`.

### Data integrity classification

The release corpus is the reproducibly generated, fictional Northstar Q2 Operations Review. It is
8 pages, 501,304 bytes, and has SHA-256
`e93f2c3a6836ac74ab8c14d8d691f2683ad936595203ada02099907f9dca367a`. It contains native text,
tables, raster charts, a scanned memo, and a process diagram. No personal, customer, production,
or confidential data was used.

### Mock and fixture usage

- The deterministic evaluation uses temporary SQLite/filesystem/Chroma state and the synthetic
  Northstar document.
- OpenAI unit tests use fake transports; three real-provider smoke tests remain opt-in and were
  deselected.
- Streamlit component behavior uses `AppTest` with a fake API client.
- The HTTP lifecycle test uses an in-process ASGI transport and is classified as integration, not
  rendered Playwright proof.

### Local container and OCR proof

- `document-intelligence doctor` returned `ready`, `deterministic`, `token-protected`, data ready,
  sample ready, and OCR ready.
- The container runs as UID/GID 10001 (`app`), with a read-only root filesystem, all capabilities
  dropped, and a writable named volume mounted at `/data`.
- The same named volume preserved the completed 8-page sample and its 90 evidence items across
  repeated image rebuilds and container recreation.
- Actual container Tesseract read the raster chart labels `APR $2.5M`, `MAY $2.7M`,
  `JUN $3.2M`; the product chart labels `CORE PLATFORM 45%`, `PRO ANALYTICS 35%`, and
  `SERVICES 20%`; and the scanned page-5 incident memo.

### Rendered browser proof

The in-app Browser exercised the final local API/UI/worker image, not a mocked frontend:

- Monthly chart reconciliation returned
  `Yes. April $2.5M plus May $2.7M plus June $3.2M equals the reported Q2 net revenue of 8.4M.`
  with page-3 chart and page-2 table-row citations.
- Product-mix reasoning returned `Core Platform was the largest product at 45% of Q2 gross
  bookings.` with a page-4 chart citation.
- Scanned-memo reasoning returned `An expired barcode-service certificate caused a 47-minute
  outage that delayed 320 orders.` with a page-5 scanned-text citation.
- Ask and Documents were exercised as primary navigation. Evidence, Preparation, and Privacy were
  opened from the compact More menu while retaining addressable routes. Documents reported 8 pages
  and 90 evidence items; Preparation reported completion.
- Desktop Browser inspection and a 390×844 Chrome render passed. The phone layout keeps all three
  compact navigation choices on one row, removes framework chrome, and has no horizontal overflow.
- A fresh post-rebuild console observation window contained zero warnings or errors. Older retained
  WebSocket/health errors aligned exactly with deliberate container recreation and were not treated
  as current runtime failures.

### Production validation status

No hosted-development, staging, production, multi-user, multi-host, TLS/identity-layer, or live
OpenAI proof was performed. Local deterministic success does not imply any of those layers.

### Localhost validation integrity

The UI remained at `http://127.0.0.1:8514` and the API at `http://127.0.0.1:8014`. Browser evidence,
container evidence, source tests, and provider boundaries are recorded separately; no local result
is relabeled as hosted or production proof.

### Warning and issue triage

Issues discovered during rendered acceptance were resolved before completion: packaged sample
resolution, non-root `/data` ownership, suggestion persistence, addressable navigation, Tesseract
page-segmentation mode, raster label legibility, concise OCR incident answers, and literal rendering
of dollar-denominated answers. The unused OpenCLIP optional extra was removed rather than advertising
an unimplemented image-vector path.

### Warning suppression status

No test warning suppression was added. The deterministic suite passes with `-W error`. Transient
browser errors produced only during deliberate container replacement are documented above rather
than hidden.
