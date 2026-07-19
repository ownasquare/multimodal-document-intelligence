# Support

Before opening an issue:

1. Follow the [Quickstart](docs/quickstart.md) once with the bundled sample.
2. Run `uv run document-intelligence doctor` for a source installation, or `docker compose ps` for
   Docker.
3. Confirm Python 3.12 and the locked environment are active when running from source.
4. Check `docs/configuration.md` and `docs/operations.md`.
5. Reproduce with `examples/northstar-q2-operations-review.pdf` if possible.

A useful report includes the command used, operating system, sanitized error code, document type
and page count, active deterministic or provider mode, and the smallest synthetic reproduction.
Never attach confidential documents, `.env` files, provider responses containing source content,
or database/vector directories.

Questions about feature design belong in a discussion. Reproducible defects belong in an issue.
Suspected vulnerabilities follow [SECURITY.md](SECURITY.md).
