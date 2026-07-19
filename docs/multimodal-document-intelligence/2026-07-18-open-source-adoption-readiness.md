# Open-source adoption readiness record

Date: 2026-07-18  
Release: 0.1.0  
Public repository: <https://github.com/ownasquare/multimodal-document-intelligence>

## Honest starting assessment

The application was technically complete as a local reference implementation, but it was not yet
ready to promote to strangers. A first-time visitor faced a source-first setup, a long landing page,
five equal-weight app destinations, a short extension note without registration examples, no
product screenshot, incomplete GitHub community templates, and no public remote.

Those were adoption blockers even though the parser, retrieval, citation, persistence, tests, and
container runtime were already substantial.

## What changed

- The README now leads with the product promise, an actual source-backed answer screenshot, and one
  five-minute Docker path that needs no API key.
- `docs/quickstart.md` separates Docker and source installation, shows the first useful question,
  explains stop/reset behavior, and keeps troubleshooting concise.
- **Ask** and **Documents** are the only primary destinations. Evidence browsing, preparation, and
  privacy live under **More** and remain addressable by URL.
- Compact `(i)` tooltips explain document scope, evidence, and provider/privacy behavior without
  adding persistent instruction panels.
- Streamlit's framework toolbar is hidden so desktop and phone layouts show only product controls.
- `docs/extending.md` maps parsers, embeddings, visual providers, answer providers, vector stores,
  retrieval behavior, and UI changes to exact contracts, registration points, and focused tests.
- GitHub issue forms, a pull-request template, code of conduct, support path, project URLs, topics,
  discussions, secret scanning, and push protection are configured.
- The public-tree checker now verifies essential adopter files, README entry points, screenshot
  presence, local Markdown links, ignored runtime data, and obvious credential assignments.

## Newcomer workflow

The intended zero-context journey is now:

1. clone the repository;
2. run `docker compose up --build -d`;
3. open `http://127.0.0.1:8514`;
4. choose **Create sample workspace**;
5. wait for **Preparation** to complete;
6. ask **Do the chart months reconcile to the reported Q2 total?**; and
7. inspect the page-3 chart and page-2 table-row evidence.

The README, Quickstart, UI, sample, and issue forms use the same language for this path.

## Verified evidence

- Deterministic gate: 123 passed, 3 live-provider tests deselected.
- Branch coverage: 81.14%, above the enforced 80% gate.
- Ruff, strict mypy, Bandit, pip-audit, lock check, wheel, and source distribution: passed.
- Public-tree checker, local link checks, Compose configuration, and diff whitespace: passed.
- Final container image:
  `sha256:38e5d1a9a19189be2e79b972a4fb114e8a851009a4ee052c76288c9c5da16736`.
- Runtime: API and UI healthy, worker running, doctor ready, Tesseract ready, loopback ports only.
- Browser: desktop Ask, More menu, Documents, Privacy, question submission, and chart/table evidence
  passed against the rebuilt container with an empty fresh console interval.
- Responsive render: 390×844 Chrome viewport passed without navigation overlap or horizontal
  overflow.
- Product screenshot: 59,847 bytes, SHA-256
  `bc74e3df3dc84d9ee455bc37428a0fe916cf89affa416324ed01a53174c141e1`.

## Public-release boundary

This record proves an understandable, installable, extensible public source release and a local
container/browser workflow. It does not prove a hosted application, live OpenAI account/model
availability, arbitrary enterprise-corpus accuracy, multi-user isolation, horizontal scaling, dark
mode, or production operations. The README and validation guide keep those boundaries explicit.

## Good next contributions

- add a safe parser fixture for a layout not covered by the Northstar sample;
- add a provider adapter behind the existing protocols and deterministic fake;
- add rendered Playwright E2E to CI once browser installation cost is intentionally accepted;
- evaluate a redistribution-safe heterogeneous document corpus; and
- add identity, tenant scoping, external persistence, and queueing before shared hosting.
