# Configuration and identity enrollment

Settings load from `PA_` environment variables and the working directory's `.env`. Process environment takes precedence. `.env` is gitignored. On Linux, protect it with mode 0600; on Windows, restrict its ACL to the service user. The SQLite cache also contains private data and is not encrypted by this application.

## Enroll the intended Plex profile

1. Set URL, exact allowed hostname, and a privately obtained Plex token. `discover` needs only these values. Use a token you are authorized to use; the app does not extract tokens from Plex's registry, files, browser, or internal database.
2. Run `plex-advisor discover`. Record the returned `server_id`, token cloud account ID and actual username. For a different target, select the exact returned cloud user ID and actual username, not the display name. Managed tokens that cannot independently prove their cloud identity are unsupported and fail closed.
3. Set `PA_PLEX_SERVER_ID`, `PA_PLEX_ACCOUNT_ID`, `PA_PLEX_USERNAME`, and selected section IDs. Only movie/TV sections are accepted.
4. To enable history, identify your local `/accounts` row from discovery. Verify the intended profile in Plex. Set `PA_PLEX_HISTORY_ACCOUNT_ID` and `PA_PLEX_HISTORY_ACCOUNT_NAME` to that exact returned pair, then set `PA_HISTORY_BINDING_CONFIRMED=true`. This explicitly attests your cloud/local association; the API supplies no cloud foreign key for this row. IDs are not assumed equal and local names may differ from cloud usernames.
5. Run `diagnose`. Compare watched state, personal rating, resume state and any history sample against your Plex interface under that profile. An empty sample proves no viewing dates. If anything disagrees, stop and correct enrollment before syncing.
6. Run `sync --full`. Only a successful run verifies the complete selected import. A partial run can retain useful metadata while reporting history unavailable.

Each connection rechecks cloud identity, pinned server ID, and the enrolled local ID/name. Every history page carries that ID, and every event is checked again. Missing/ambiguous local mapping disables history; it does not silently aggregate users. Token-scoped catalog state can still be imported after cloud identity verification. Re-enroll after known account recreation or server database restoration: local ID/name reuse cannot be detected independently.

The SQLite identity binding includes origin, server, cloud ID/name, local history ID/name, confirmation and sections. Changing any of these requires a **separate database path**. Preserve the original database and its feedback; there is no automatic cross-account merge. Configure enrollment before creating the first cache, including before `status` or `serve`.

## Settings

| Variable (prefix `PA_`) | Default / meaning |
|---|---|
| `PLEX_URL` | `http://127.0.0.1:32400`; operator-controlled origin only |
| `PLEX_ALLOWED_HOSTS` | JSON array `["127.0.0.1"]`; exact hostname match |
| `PLEX_TOKEN` | Secret; required for discovery/sync |
| `PLEX_ACCOUNT_ID`, `PLEX_USERNAME` | Exact verified cloud identity |
| `PLEX_SERVER_ID` | Pinned server machine identifier |
| `PLEX_HISTORY_ACCOUNT_ID`, `PLEX_HISTORY_ACCOUNT_NAME` | Independently enrolled local account |
| `HISTORY_BINDING_CONFIRMED` | `false`; explicit operator attestation |
| `LIBRARY_SECTIONS` | JSON array of positive section IDs |
| `DATABASE_PATH` | `data/plex-advisor.sqlite`, relative to working directory |
| `SYNC_INTERVAL_SECONDS` | `14400`; minimum 60 |
| `PAGE_SIZE` | `200`; range 1–1000 |
| `TIMEOUT_SECONDS` | `20`; range 1–120 |
| `TIMEZONE` | `UTC`; example `.env` uses `America/New_York` |
| `MCP_HOST`, `MCP_PORT` | `127.0.0.1`, `8765` |
| `AUTH_MODE` | `local` or `oauth`; no anonymous mode |
| `LOCAL_BEARER_TOKEN` | Independent random secret, at least 32 characters |
| `OAUTH_ISSUER`, `OAUTH_JWKS_URL` | Exact trusted HTTPS identity-provider endpoints |
| `OAUTH_RESOURCE` | Exact canonical HTTPS MCP audience/resource, including `/mcp` |
| `OAUTH_SUBJECT` | Exact allowed provider user subject; only one principal |
| `MCP_ALLOWED_HOSTS` | Additional exact HTTP Host values for approved deployment |
| `REQUESTS_PER_MINUTE` | `120`; per-minute quota for each of authenticated and anonymous requests, per process |
| `LOG_LEVEL` | `INFO`; provider HTTP logging suppressed even in DEBUG |

Lists use JSON syntax. Do not put tokens in URLs. Redirects are denied. TLS certificate verification stays enabled. Prefer HTTPS for Plex across untrusted networks; plain HTTP loopback/LAN requires a trusted network. Do not expose Plex port 32400.
