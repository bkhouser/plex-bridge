# Verification

## Automated checks

Install development dependencies and run the commands in [CONTRIBUTING](../CONTRIBUTING.md).
Tests use synthetic fixtures, generated test keys, temporary SQLite databases,
and a temporary loopback listener; no Plex/OAuth credentials are needed. Permit
local sockets and use a fresh writable pytest `--basetemp` in restricted environments.
See [release review](release-review.md) for actual observed results and limitations.

## Your deployment's acceptance checks

1. Discover and independently verify the intended profile and selected libraries.
2. Compare a known personal rating with Plex's UI, separately from public ratings.
3. Compare recent titles/dates/timezone under that same profile.
4. Test pagination, canonical duplicates and ambiguous title/year lookup.
5. Confirm missing ratings/history remain unknown, not fabricated or zero.
6. Verify authenticated calls work; anonymous/wrong-subject calls must be denied.
7. If using feedback, explicitly save a clearly identified test opinion, inspect it,
   restart, verify persistence, then explicitly delete it. Plex must remain unchanged.
8. In a test deployment, simulate Plex being unavailable: preserve cached data with
   a stale warning; restore access and verify a successful sync clears the warning.
9. Verify reboot/sign-in startup and reconnect before relying on unattended use.

Example prompts: “Show recent viewing history with freshness,” “What is my personal
rating for this exact title/year?” and “Find unwatched movies under 100 minutes.”

Optional `scripts/verify_live_cache.py` and `scripts/verify_live_mcp.py` inspect your
configured private deployment. Review them before use and keep output private.
Synthetic tests do not prove your server's permissions or history completeness.
A GitHub release does not imply directory review or approval.
