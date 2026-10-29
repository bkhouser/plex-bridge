# Advanced historical recovery

Ordinary sync never authorizes retired libraries or infers missing identity.
Schema 3 supports explicit event-specific provenance for owner-confirmed retired
libraries. This is an advanced local-operator workflow, not an MCP tool or routine
installation step. There is no built-in approved owner, cohort, count or cutoff.

Construct `RecoveryApproval` with exact audited mappings
`(retired_section, selected_section, media_type, event_count)`, an exclusive UTC
epoch cutoff, operator name and actual owner confirmation. Pass approval separately
to both `prepare(..., approval=...)` and `apply(..., approval=...)`. Never derive
approval from an untrusted plan. A checksum is not a signature or authorization.

Preparation uses a read-only cache snapshot and verified Plex account/server. It
checks retired IDs are absent, targets selected with matching types, exact counts,
and bounded pagination. Each event must already exist with matching owner/server,
event ID, timestamp, type and title. No current item identity is inferred. Changed
records, unexpected keys, reused sections, or missing/repeated pages stop recovery.

Privately review the complete plan. Before applying, create a SQLite-consistent
backup, check integrity/foreign keys and rehearse on a restored copy. Apply writes
provenance sidecars atomically; original events, ratings, state and feedback stay
intact. Independently supplied approval must match the plan. Version-2 plans
replace private prototype version-1 plans; already enrolled evidence stays readable.

Only exact enrolled events with active mappings, matching account/server/date/type
and a selected target are included. Results retain original `section_id` and expose
`selected_section_id` separately. Titles/episode numbers are display evidence,
not proof of linkage or current availability.

## Revocation and rollback

With owner authorization, call `revoke(db, exact_mapping_id, reason)` locally.
The next history query excludes the mapping while evidence remains stored. Neither
replaying an old plan nor a new plan can overwrite evidence or undo revocation.
Do not edit the cache identity table to bypass enrollment.

For rollback, stop the application and restore matching source/database backups.
Do not run schema-2 code against a schema-3 database. Retain private audit copies.
Historical playback does not establish current watched state, availability or liking.
