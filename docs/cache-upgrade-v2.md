# Cache upgrades and schema compatibility

Current schema version is 3. Opening an existing database applies additive migrations automatically. The notes below describe schema 2; schema 3 adds historical provenance tables without enrolling any events. See [historical recovery](historical-recovery.md).

## Before activating

Stop the old service/scheduler and any other process using this cache. Preserve a
private SQLite-consistent backup, including local feedback. Use SQLite's backup
API, or copy files only after all connections close and WAL checkpointing is
complete. Do not copy only the main file from a running WAL database.

Opening a database through the updated `Database` constructor automatically runs
the versioned migration. This includes CLI `status`, `sync` and `serve`; `status`
is not a migration dry run. First validate an operator-created backup copy with
the new code and the identical account/library binding. Do not run old and new
code concurrently against one cache. Older code rejects a newer schema; rollback requires
the old source and the preserved schema-1 backup, not a manual version change.

## What the migration does

- Adds event library section, parent/show keys and titles, and season/episode
  numbers. It resolves missing fields only through matching cached server,
  rating key and media type. Retained unavailable catalog entries are eligible.
- Keeps unmatched history records but excludes them from history, media history
  summaries, recent activity and status event counts. The number excluded appears
  in `freshness.excluded_history_events` with a warning. No section is guessed from
  a title, another account, or the fact that a request included a section filter.
- Preserves original public-rating slots in `public_ratings` and classifies each
  provider/metric. IMDb/TMDB/TVDB scores are audience metrics; recognized Rotten
  Tomatoes icons distinguish critic and audience metrics. Unknown sources remain
  available as `metric="unknown"`, rather than becoming critic scores.
- Does not modify Plex personal ratings, watch state, local feedback, credentials,
  configuration or Plex itself. The schema and metadata transformation are one
  transaction; failure leaves that migration unapplied.

A full cache rebuild is **not required**. After backup and an approved activation,
a full synchronization is recommended to recover TV identity omitted by version
1 and enrich older retained events with explicit section identities. All catalog
pages are refreshed even on ordinary synchronization, but older history outside
the overlap window requires a full scan. History that Plex no longer returns and
that has no matching cached item remains excluded; a full scan cannot manufacture
missing evidence. Feedback is retained independently.

## Tool-result compatibility

Existing personal-rating and watched/resume fields retain their meanings. In
particular, `watched=false` may still have a resume offset and is not proof of never
having started or seen a title. Authentication, account binding, GET allowlists and
the feedback-write scope/explicit-authorization boundary are unchanged.

`critic_rating` and `audience_rating` now contain only classified metrics. A score
formerly mislabeled as critic may move to audience, or become null in both aliases
when the source is unknown. Its original value/source remain in `public_ratings`.
If both raw slots are audience scores, the audience alias prefers Plex's native
audience slot; inspect the list to reconcile individual providers. No live IMDb
lookup or upstream rating refresh is performed.

Read-only MCP tools use one database snapshot for data and freshness. A response
may correctly describe the previous snapshot if a refresh commits concurrently;
it will carry that snapshot's freshness. Long reads can temporarily retain WAL
pages while a sync proceeds.

Catalog and history results add TV hierarchy fields. Episode searches can match
show/season titles and filter by exact server-backed `show_id`, `season_number`
and `episode_number`. Missing legacy fields remain null until verified enrichment.

Taste profiles include explicit ratings from available movies, shows, seasons and
episodes, with media types and server-backed IDs retained. Recommendation
exclusions accept physical or canonical IDs and exclude all verified copies,
including when the supplied physical copy was later removed. `matching_titles`
now counts after exclusions; `matching_titles_before_exclusions` preserves the
pre-exclusion count. Legacy titles without shared canonical GUIDs remain distinct.
