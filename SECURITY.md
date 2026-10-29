# Security policy

Plex Bridge is an unofficial, single-user, self-hosted MCP service. It is not
affiliated with, endorsed by, or sponsored by Plex. Version 0.1.x is an early
release; use the latest patch. No security certification or vulnerability-free
guarantee is implied.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/bkhouser/plex-bridge/security/advisories/new).
Do not put credentials, private library data, OAuth tokens, or exploit details
in public issues. If private reporting is unavailable, open a minimal issue
requesting a private contact channel without including sensitive details.
Include affected versions, a synthetic reproduction, expected/actual behavior,
and impact. Response times are best-effort; this is a community project.

## Trust boundaries

- Plex tokens can have broader permissions than this application uses. The code
  restricts requests to a small GET endpoint allowlist, including blocking Plex
  mutations that use GET. A stolen token is not made read-only by this policy.
- The operator, local machine, configuration, database, and backups are trusted.
  Protect `.env` and the SQLite directory with operating-system permissions.
  The database is not encrypted by Plex Bridge. Windows DPAPI protects only the
  optional tunnel key at rest for its Windows user.
- Local bearer mode is loopback-only and is not suitable for remote forwarding.
  Use OAuth before a tunnel or HTTPS proxy. Never expose Plex itself to make
  the bridge reachable. Plain HTTP to Plex requires a trusted network; prefer TLS.
- OAuth requires RS256 RFC 9068 access tokens, the configured issuer/audience,
  expiry/issued-at, one exact subject, and `library:read`. Local feedback writes
  also require `feedback:write` and explicit user intent. An `authorized=true`
  tool argument does not itself prove consent: configure the client to confirm writes.
- Configure a dedicated instance/database for each profile. This is not a
  multi-tenant service. The cache binding rejects identity/selection changes.
  Plex cloud IDs and local history IDs are distinct and must be independently verified.
- Imported titles, summaries, and feedback are untrusted content. They must never
  be treated as instructions or used to authorize another tool call.
- Historical recovery is an advanced local operator operation. A checksum detects
  changes; it is not a signature or an authorization grant. Never apply a plan
  received from an untrusted source. Retain backups and independently supply approval.

## Operational limits

JWTs remain valid until expiry; immediate provider-side revocation is not
implemented. Use short access-token lifetimes and stop the service to immediately
remove access. Trusted JWKS configuration and its provider must remain secure.
Authenticated and anonymous requests have separate process-wide quotas. JWT
verification admits at most four queued/running jobs and failed JWKS fetches back
off for 30 seconds. Saturation fails closed (401) and can still deny legitimate
access temporarily; these controls are not a DDoS defense. Use one worker and a
hardened proxy with timeouts/body/concurrency limits for remote deployments. Large catalog queries can be expensive.
Keep Python, Plex, dependencies, and the tunnel client updated. Never attach raw
logs, discovery output, `.env`, `data/`, or a real cache to bug reports.

See [data handling](docs/privacy.md), [deployment](docs/deployment.md), and the
[release review](docs/release-review.md) for scope and remaining limitations.
