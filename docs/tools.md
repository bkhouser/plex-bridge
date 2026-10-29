# MCP tool contract

All tools require `library:read`. Names and parameters are discoverable through
MCP `tools/list`. Personal data and freshness are read from one SQLite snapshot.
`get_usage_guide` returns static documentation without querying the cache.
Queries do not contact Plex; the background sync normally refreshes every four hours.

| Tool | Purpose / limits |
| --- | --- |
| `get_usage_guide` | Built-in help: eight example requests, tool workflows, data meanings and consent boundaries. No arguments or personal data. |
| `get_plex_status` | Cached account, capability evidence, last sync, stale flag, counts and excluded-history warnings; not a live connectivity probe. |
| `search_library` | Movies/shows/seasons/episodes; title/summary, genres, people, years, runtime, watched state, personal ratings, section and TV hierarchy. Up to 100 copies/page, exact copy/title counts and next offset. |
| `get_media_details` | One exact `server:ratingKey`; metadata, separate ratings, history summary, copies and local feedback, including retained unavailable entries. |
| `get_watch_history` | Enrolled account/library events; aware start/end timestamps (end exclusive), TV identity, timezone and pagination up to 100/page. |
| `get_library_statistics` | Movie copy counts, genres/years/decades, personal ratings and recent scoped activity. Genre totals overlap. |
| `check_library_availability` | Up to 50 movie title/year pairs; exact normalized matching with explicit ambiguity. No fuzzy/title-translation guesses. |
| `get_taste_profile` | Explicit ratings/opinions separated from weaker viewing signals; bounded samples. |
| `get_recommendation_candidates` | Optional movie-only factual helper; up to 50 canonical candidates, filters, keyword evidence, exclusions and diversity seed. Assistant makes final judgments. |
| `get_media_feedback` | Paginated explicit local opinions, optionally for an exact media ID; retained after catalog removal. |
| `record_media_feedback` | Explicit local append/correction. Requires `feedback:write`, `authorized=true`, an exact media ID and comments. |
| `delete_media_feedback` | Delete one inspected local feedback record. Requires `feedback:write` and `authorized=true`; destructive and idempotent. |

Examples to ask your assistant:

- “Use Plex Bridge to show my recent viewing history, including cache freshness.”
- “Find movies under 100 minutes in my library that aren't marked watched.”
- “What is my personal rating for this movie? Keep it separate from public ratings.”
- “Remember that I liked this film's dialogue.” (An explicit local feedback write.)

Unwatched does not mean never seen. Rewatching does not prove liking. Missing data
is unknown, not zero. Stored content is untrusted data, not executable instructions.

## Documentation inside MCP

Initialization instructions point assistants to `get_usage_guide` for onboarding.
Clients that support MCP resources can discover the same Markdown guide through
`resources/list` and read `plex-bridge://guides/getting-started` with `resources/read`.
The help tool works for clients that only expose tools. Both surfaces require the
normal authenticated connection, and reading an example never authorizes its actions.

After upgrading/restarting the server, refresh the client's tool list or reconnect
to discover the new tool. Existing installations are not updated by a source edit.
