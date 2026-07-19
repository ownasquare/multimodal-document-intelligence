# Quickstart

This guide takes you from a clean checkout to one source-backed answer. No API key is required.

## Recommended: Docker

Install Docker Desktop or another Docker Compose-compatible runtime, then run:

```bash
git clone https://github.com/ownasquare/multimodal-document-intelligence.git
cd multimodal-document-intelligence
docker compose up --build -d
docker compose ps
```

The `api` and `ui` services should become healthy and the `worker` should remain running. Open
[http://127.0.0.1:8514](http://127.0.0.1:8514).

1. Choose **Create sample workspace**.
2. In **Preparation**, wait until the job says **Complete**.
3. Open **Ask**.
4. Choose the suggested question **Do the chart months reconcile to the reported Q2 total?**
5. Choose **Ask documents**, then open the evidence under the answer.

The bundled PDF includes native text, tables, raster charts, a scanned incident memo, and a process
diagram. Docker includes Tesseract OCR, so the full sample path works without host setup.

Stop the workspace without deleting its documents:

```bash
docker compose down
```

Reset all local Document Intelligence data only when you intentionally want a clean workspace:

```bash
docker compose down --volumes
```

## Source installation

Requirements:

- Python 3.12 (3.13 is not yet supported)
- [uv 0.8.17 or newer](https://docs.astral.sh/uv/)
- optional Tesseract 5 for scanned pages

```bash
uv sync --all-groups --frozen
make demo
```

`make demo` generates and loads the sample idempotently, starts the API on `127.0.0.1:8014`, starts
the worker, and opens the UI on `127.0.0.1:8514`. Keep that terminal open while using the app.

To inspect each process independently, use three terminals:

```bash
make api
make worker
make ui
```

## Add your own PDFs

Open **Documents** and add up to ten PDFs. A file can contain at most 250 pages and be at most
50 MiB by default. Ask questions after its status is **Ready** or **Ready with notes**.

Deterministic mode supports the complete sample, extraction, indexing, retrieval, and evidence
inspection without credentials. For open-ended answers over arbitrary documents, follow
[Configuration](configuration.md#openai-mode) to enable OpenAI.

## If something does not start

Run:

```bash
uv run document-intelligence doctor
```

Then check:

- **UI cannot reach the service:** start the complete workspace with `make demo` or confirm the
  Compose services are healthy with `docker compose ps`.
- **Scanned text is missing in a source install:** install Tesseract 5 and confirm `tesseract` is on
  the command path. Native text and tables still work without it.
- **A PDF remains in preparation:** open **More → Preparation** for the plain-language stage and
  retry option.
- **OpenAI mode fails at startup:** confirm the three `DOCINTEL_` settings in
  [Configuration](configuration.md#openai-mode); keys must stay in an ignored `.env` file.

For a sanitized bug report, follow [Support](../SUPPORT.md). Never attach a confidential PDF or
credential to a public issue.
