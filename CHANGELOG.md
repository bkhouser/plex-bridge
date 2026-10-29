# Changelog

## 0.1.1 — 2026-10-29

First public source release, under the Plex Bridge name (formerly Plex Advisor).

- Authenticated MCP access to selected personal libraries, ratings, and viewing history.
- Read-only Plex endpoint policy, pinned account/server identity, and explicit cache freshness.
- Optional explicit local feedback and factual movie candidate/taste helpers.
- Schema-3 historical evidence support with operator-supplied recovery approval;
  no built-in personal authorization or recovery cohort.
- Public installation, client, privacy, security, contribution, and release documentation.
- Automated checks and clean source/plugin packaging.

Compatibility identifiers remain `plex-advisor`, `plex_advisor`, and `PA_`.
Existing databases are retained. Historical plan version 1 cannot be applied by
the generic public recovery helper; prepare a new plan with explicit approval.
Existing enrolled evidence and revocations continue to be read without rewriting it.

This release is a self-hosted bridge, not an official Plex product or an approved
OpenAI directory listing. See [release review](docs/release-review.md).

- Reject unsupported Plex response shapes, ambiguous empty pages, duplicate IDs, nonfinite values and unexpected media types while preserving the affected cache.
- Validate local cache schema/integrity before writes and reject orphaned identity rebinding. Surface redacted, actionable compatibility errors.
- Isolate anonymous/owner rate quotas, bound JWT verification admission, and back off failed JWKS fetches.
- Require a patched certifi trusted-certificate bundle.
