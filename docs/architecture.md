# Architecture

```text
Verified Plex user session --GET allowlist--> Plex API
          |
          v
page-bounded synchronization --> SQLite WAL cache <--- explicit local feedback
                                      |
                           authenticated MCP tools
                                      |
                       ChatGPT / compatible MCP client
```

`plex_client.py` is the only Plex adapter. Python-PlexAPI handles server/account authentication and queries; the adapter parses the returned XML explicitly to avoid lazy owner-context fetches and accidental filesystem fields. Catalog pages include GUIDs, metadata and user state. TV sections are imported as shows, seasons and episodes.

`security.py` enforces GET-only requests to configured Plex origin paths and a small fixed set of `plex.tv` account endpoints. Redirects and environment proxies are disabled. Even GET-based scrobble/rating operations are denied. Credentials are held in memory; tokens never enter the database. Provider exceptions are converted to fixed error codes. CLI validation errors omit input values.

## Persistence

Schema version 3 is in `database.py`'s ordered `MIGRATIONS` list and recorded by SQLite `user_version`. Foreign keys are enabled on every connection. Future releases append migrations; newer unsupported databases are refused. Before upgrading, stop the service and make a private backup using SQLite's backup API, or copy the database only after clean shutdown and checkpoint. Never copy just a live main file while ignoring its WAL. See [schema-2 activation and compatibility](cache-upgrade-v2.md). Schema 3 adds event-specific historical provenance; see [historical recovery](historical-recovery.md).

Physical media IDs are `serverIdentifier:ratingKey`. Canonical grouping uses server-scoped `plex://` GUIDs; absent/legacy GUIDs conservatively use the physical ID. Titles alone are never identity. Distinct editions keep their own physical records, availability and state. External identifiers are retained for future enrichment but do not trigger speculative merges.

Normalized genres, people and GUID tables support indexed joins. `user_media_state` is separate from `viewing_events`. Qualitative evaluations reference `canonical_media`, independent of physical availability and Plex ratings. Synchronization never updates the feedback table. No media row is deleted on removal.

## Synchronization and failure behavior

A first/full sync captures all retrievable history. Incremental refresh scans every selected catalog page to catch user-state changes that do not update metadata timestamps. Fingerprints identify changes; unchanged rows only update their seen marker. History uses a two-day overlap with stable-event deduplication. Run `sync --full` to reconcile older backfills/imports.

Metadata processing is page-bounded; a set of stable IDs grows with each collection to detect duplicate/replayed pages. A SQLite write transaction serializes syncs while WAL readers continue using the prior committed snapshot. Successful sections can commit even if another fails. Each section has a savepoint: an incomplete section cannot mark unseen titles removed. History and its cursor share another savepoint; an identity mismatch on a later page rolls back the entire history import. An interrupted process rolls back the transaction.

Stable event keys are mandatory. No date is manufactured from watched status. Empty personal ratings remain null. PMS omits zero movie/episode `viewCount`, so parsing follows PlexAPI's zero-default semantics; show/season watched state uses leaf counts. History timestamps come from epoch values converted directly to UTC, independent of host timezone. User date bounds must be aware, with exclusive end; results can render in an IANA timezone.

Catalog removal is based on a complete successful section traversal. Changing collection totals, repeated pages, wrong offsets and premature empty pages cause rollback. PMS offers no transactional snapshot across pages: same-count concurrent library edits may require another refresh. No metadata endpoint is called during ordinary MCP queries.

`last_successful_sync` means the complete run succeeded. Partial/failed/old caches report a warning. No status tool actively probes Plex, so connection capability evidence is explicitly tied to synchronization.

Each read-only MCP tool pins data and freshness to one read-only SQLite snapshot.
History queries are section-scoped at import and filtered by the database's bound
account/server/sections at read time. Unmatched legacy events remain stored but
are excluded with a count and warning. TV hierarchy is retained for both catalog
and historical events. Public rating values retain their raw Plex slots alongside
provider/metric classifications; unknown providers are never labeled critic scores.

## Recommendations

SQL applies factual filters. `genres_all` supports Action AND Thriller; `min_view_count=4` finds more than three plays. Candidate selection scans bounded pages and retains a bounded top set. Scoring uses metadata keyword hits, explicit personal ratings and stored feedback—not public popularity. Canonical copies are deduplicated. The client can vary a seed and exclude prior suggestions. No hidden user history of recommendations is recorded.

The assistant first reads the taste profile, then uses candidates and explicit feedback to reason about subjective qualities. Rewatch counts are weak evidence. External titles require explicit availability checks. Search exposes both physical-copy and verified canonical-title counts; legacy copies without canonical GUIDs may remain distinct.

## Authorization

Local mode uses an independently generated opaque bearer and binds only to loopback. It advertises no nonexistent OAuth server. Remote/tunnel mode uses the SDK's resource-server middleware and a fixed provider's RS256 RFC 9068 access tokens (`typ=at+jwt`). Signature, issuer, audience, expiry, issued-at, exact subject and `library:read` are verified for each request. Feedback writes additionally check `feedback:write` inside tool execution. Missing scopes cannot be bypassed by setting `authorized=true`.

The application does not implement an authorization server. No tunnel or proxy header can override the configured Plex identity. Host/Origin validation, a 64 KiB MCP request limit, bounded inputs/results and separate authenticated/anonymous quotas are active. A process-shared four-slot admission bound covers queued and running JWT verification, including cancellation; failed JWKS fetches have a 30-second cooldown. Saturation fails closed with 401. SQLite files, service credentials and the local machine remain within the operator's trust boundary.

## Compatibility failures

See [compatibility and recovery](compatibility.md). Response-contract failures use fixed error codes, preserve the affected cache, and do not advance full-success freshness. Cache structure, key constraints, version, JSON capability and integrity are checked before migrations; orphaned personal caches cannot be rebound.
