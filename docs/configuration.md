# Configuration

Settings use the `DOCINTEL_` prefix and may be stored in an ignored `.env` file. `.env.example`
contains safe non-secret defaults. Never commit a real key or token.

## OpenAI mode

Copy `.env.example` to `.env`, then set the answer/vision mode, embedding mode, and provider key:

```text
DOCINTEL_PROVIDER_MODE=openai
DOCINTEL_EMBEDDING_PROVIDER=openai
DOCINTEL_OPENAI_API_KEY=your-example-key
```

Restart the API and worker after changing provider settings. Relevant retrieved excerpts and
selected page or crop images may leave the machine in this mode; entire PDFs are not uploaded to a
persistent provider file store.

## Settings reference

| Setting | Default | Purpose |
|---|---|---|
| `DOCINTEL_ENVIRONMENT` | `development` | `development`, `test`, or `production` behavior |
| `DOCINTEL_DATA_DIR` | `.data` | SQLite, upload, artifact, and Chroma root |
| `DOCINTEL_API_HOST` | `127.0.0.1` | API bind host |
| `DOCINTEL_API_PORT` | `8014` | API port |
| `DOCINTEL_UI_PORT` | `8514` | Streamlit port |
| `DOCINTEL_API_BASE_URL` | derived | API URL used by the Streamlit server |
| `DOCINTEL_API_TOKEN` | unset | Required whenever API host is not loopback |
| `DOCINTEL_PROVIDER_MODE` | `deterministic` | `deterministic` or `openai` answer/vision path |
| `DOCINTEL_EMBEDDING_PROVIDER` | `deterministic` | `deterministic` or `openai` text embeddings |
| `DOCINTEL_OPENAI_API_KEY` | unset | Server-side provider credential |
| `DOCINTEL_OPENAI_CHAT_MODEL` | `gpt-4o-mini` | Configurable answer model |
| `DOCINTEL_OPENAI_VISION_MODEL` | `gpt-4o-mini` | Configurable visual-description model |
| `DOCINTEL_OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Text-only embedding model |
| `DOCINTEL_ENABLE_OCR` | `true` | Attempt selective OCR when Tesseract exists |
| `DOCINTEL_DEMO_MODE` | `true` | Enable idempotent synthetic sample loading |

Default safety limits are 50 MiB per PDF, 250 pages per PDF, ten files per batch, one worker slot
configurable up to two, 4,000 question characters, 12 conversation-history turns, and ten
retrieved evidence items. The typed settings class exposes advanced overrides for controlled
development, but the UI always shows the effective file/page/batch limits.

Non-loopback binding without `DOCINTEL_API_TOKEN` fails configuration validation. OpenAI answer or
embedding mode without `DOCINTEL_OPENAI_API_KEY` also fails before accepting work.
