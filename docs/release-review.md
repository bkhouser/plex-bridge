# v0.1.1 release review

Reviewed 2026-10-29 for the first public source release of Plex Bridge.

## Scope

The review covered all application modules, the Plex adapter and allowlist,
identity enrollment, OAuth/local authentication, MCP tools, query construction,
synchronization and recovery transactions, schema migration, startup/tunnel
helpers, plugin packaging, dependencies, and publication documentation. A separate
security review checked authentication, availability limits and compatibility
boundaries. This is an engineering review, not a third-party security certification.

## Findings addressed

- Removed personal deployment information and inherited historical-recovery
  approval from public source. Recovery now requires independent, immutable,
  operator-supplied approval at preparation and application.
- Separated authenticated and anonymous request quotas. Bounded queued/running
  JWT verification to four jobs, retained slots across cancellation, and added
  failed-JWKS-fetch backoff without extending cached-key validity.
- Updated the trusted-certificate dependency past GHSA-6q48-6c7w-9gw2 and added a
  runtime minimum version. Disabled environment proxy use in the local smoke client.
- Rejected malformed/ambiguous Plex responses, repeated IDs, wrong media types,
  inconsistent pagination and nonfinite numeric fields. Affected sections/history
  roll back and expose redacted compatibility guidance.
- Validated cache schema/constraints/integrity before writes or migration.
  Orphaned personal caches cannot migrate or bind to a new account. Runtime cache
  errors are redacted and actionable. Supported migrations preserve data.
- Corrected the optional live verifier to compare selected-library retrievable
  events rather than equating an unscoped server total with retained cache history.
- Added explicit packaging allowlists, an indexed-file publication guard, pinned
  CI actions, dependency auditing, Dependabot and private vulnerability-reporting
  instructions.

## Validation

The release validation uses synthetic fixtures and generated test keys, a separate
Python 3.12 Windows environment, and an isolated loopback MCP listener. It does
not run a new import against a real Plex server or alter personal feedback.

- Full pytest suite: **191 passed**, including API/cache drift, account isolation, OAuth
  signature/claims, cancellation, savepoints and schema upgrades.
- Eleven synthetic Windows startup identity/ownership checks.
- Ruff lint/format and strict mypy across application modules.
- `pip-audit` on the installed release environment: no known vulnerabilities after
  the certificate-bundle update. This is a point-in-time result, not a guarantee.
- Official OpenAI schemas validate both plugin manifests.
- Built wheel, sdist, plugin/source ZIPs; inspected archive contents and private
  exclusions. Scanned the exact indexed public tree using Gitleaks 8.30.1: no findings; separately compared against private configuration values.

GitHub Actions repeats the tests, lint/type/package checks on Windows and Linux
with Python 3.12/3.13, plus dependency and manifest audits. Consult the workflow
run for the exact release commit for its current status.

## Remaining boundaries

Single-user/self-hosted only. Remote installations need their own correct OAuth,
TLS/tunnel, proxy limits, filesystem permissions and backups. Verification
saturation can temporarily deny legitimate users; this is not DDoS protection.
JWT revocation is bounded by expiry. No schema checker can detect every silent
upstream semantic change. Recheck known Plex examples after upgrades.

One Starlette TestClient/httpx deprecation warning remains in the test harness;
it does not fail tests. Each real deployment must complete its own acceptance
checks. Existing running services need an explicit upgrade/restart to use these
changes. This release does not publish a hosted service or submit a directory listing.
