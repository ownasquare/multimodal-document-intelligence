# Security Policy

## Reporting

Do not open a public issue for a suspected vulnerability. Use the private security-reporting
channel of the repository host and include affected versions, reproduction steps using synthetic
data, impact, and any mitigation already tested.

## Supported release

Security fixes target the current `main` branch and most recent tagged release.

## Security boundary

The default service binds to loopback. Public exposure is unsupported without TLS, an
identity-aware access layer, a strong application token, request-rate controls, and host-level
storage protections. The project is single-user and does not provide tenant isolation.

PDFs are untrusted. The application does not execute PDF JavaScript, attachments, launch actions,
macros, source instructions, or model-proposed tools. File type, size, page count, storage paths,
document scope, evidence IDs, and deletion readback are server-controlled.

Provider credentials are server-side only. Logs and API errors must not contain provider keys,
application tokens, complete document passages, or raw internal paths. Run `make security` and
`uv run python scripts/check_public_repo.py` before release.

The runtime dependency rationale, including the Chroma security pin and its upgrade gate, is
documented in [Security and privacy](docs/security.md#chroma-dependency-hardening).
