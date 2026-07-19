# Contributing

Document Intelligence welcomes focused issues and pull requests that preserve grounded evidence,
privacy boundaries, and deterministic validation.

## Development flow

1. Install Python 3.12 and uv.
2. Run `uv sync --all-groups --frozen`.
3. Create a branch from `main`.
4. Add a failing test before changing behavior.
5. Keep provider calls behind the existing protocols and deterministic fakes.
6. Run `make check` before opening a pull request.

Start with the [Quickstart](docs/quickstart.md) if you have not run the sample yet. The
[Extension guide](docs/extending.md) maps common changes to their exact protocol, registration
point, focused tests, and compatibility rules.

Pull requests should describe the user-visible behavior, data or provider boundary affected, tests
run, and any proof not performed. Never use customer or personal PDFs as committed fixtures. Add or
extend synthetic generators instead.

## Design expectations

- Keep the primary interface centered on documents, questions, and evidence.
- Put model, vector, trace, and parser internals under Settings or Details.
- Treat every source passage and PDF object as untrusted data.
- Build citations from durable server evidence rather than generated labels.
- Keep deterministic, live-provider, local container, hosted, and production proof separate.
- Use Playwright for E2E. This Python/Streamlit project does not use Cypress.

## Compatibility

Python support is intentionally pinned to 3.12 for reproducible native/document-processing
dependencies. Parser or embedding-profile changes must create a new profile fingerprint and prove
clean reindex behavior; never mix incompatible vectors in an existing collection.

## Good first contributions

- Reproduce and fix an issue with the bundled Northstar PDF.
- Improve a plain-language error or contextual help tooltip.
- Add a synthetic parser fixture for an unsupported PDF layout.
- Add a golden retrieval question with a clear expected citation.
- Clarify one installation or extension step that was confusing in a clean checkout.

Keep each pull request focused enough that its document input, expected behavior, and evidence can
be reviewed together.
