# Plex and cache compatibility

Plex Bridge uses Plex's HTTP API through Python-PlexAPI. It never reads or edits
Plex Media Server's internal database. Changes to Plex internals matter only when
they affect the API, identity, or semantics exposed through it.

## Detectable API changes

The adapter validates XML containers and required fields, library identities and
types, pagination counts/offsets, stable unique item/event IDs, requested media
types, finite numeric values, and the enrolled account/server. Unknown additive
fields are ignored. A missing count is not interpreted as an empty collection;
an explicitly reported empty collection remains supported.

An unsupported response produces a fixed `plex_api_incompatible` error category.
The affected catalog section rolls back, including changes to availability.
Failed history imports roll back all new events and their watermark. Successful
independent sections may still commit. The last fully successful sync timestamp
does not advance, and clients receive stale/incomplete warnings and a suggested
action. Raw responses, tokens, and provider exception text are suppressed.

Missing or changed account fields fail closed. No alternate profile is guessed.
The reported Plex version, when available, is informational: a version change
alone is not proof of incompatibility. Optional omitted values retain their
documented null/zero semantics; their absence alone is not an error.

## Local cache changes

The application validates the cache before changing journal mode or migrating:
supported schema version, expected tables/column order/types/keys/foreign keys,
unique indexes, SQLite JSON support, identity JSON, and SQLite quick integrity
check. Newer or negative versions, foreign databases, and changed schema
structures are rejected. A nonempty cache with a missing identity binding cannot
be assigned to a new account. There is no automatic destructive rebuild.

Supported migrations are additive and transactional. SQLite failures encountered
after startup produce redacted cache errors through the CLI or MCP tool results.
These errors can also indicate disk, permission or lock problems; do not assume
every database error is a schema change. CLI compatibility failures exit with
code 3. Failed/partial sync commands exit with code 1 and structured error codes.

## Recovery after an upgrade

1. Stop the bridge if identity or cache compatibility is in doubt. Preserve a
   SQLite-consistent backup, including feedback and historical evidence.
2. Record application, Python, Plex and PlexAPI versions without publishing
   credentials, raw responses or private history.
3. Check release notes and update Plex Bridge/dependencies in a separate test
   environment. Recheck enrollment after server restoration or account recreation.
4. Run diagnostics and compare a known rating, watched state and history sample
   with Plex's intended profile. Then run `sync --full` and inspect freshness.
5. If the cache is incompatible, restore a matching application/cache backup or
   upgrade to a supporting application. Do not edit `user_version`, identity, or
   database tables to bypass the checks. Do not delete your original cache.

No adapter can automatically detect every silent semantic change if the response
shape remains valid. Recheck known examples after Plex/PlexAPI upgrades. Report
reproducible incompatibilities with synthetic examples. These safeguards are
covered by `tests/test_compatibility.py` and the migration/identity regression tests.
