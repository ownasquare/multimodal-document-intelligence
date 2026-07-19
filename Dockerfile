# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.8.17 AS uv

FROM python:3.12.8-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl libgomp1 tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY examples ./examples
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
    && chown -R app:app /app

COPY scripts ./scripts
COPY .streamlit ./.streamlit

USER app
EXPOSE 8014 8514

CMD ["document-intelligence", "api"]
