# Data handling and privacy

Plex Bridge is self-hosted software, not a hosted service operated by this repository.
It has no application telemetry or analytics endpoint. Operators are responsible
for their deployment, access controls, backups, and any notices owed to their users.

## Data flow

The bridge uses your privately configured Plex token to verify identity and read
selected movie/TV libraries and the enrolled profile's state. Account verification
contacts Plex cloud endpoints; library/history reads contact your configured server.
It stores metadata, personal ratings, watched/resume state, retrievable playback
events, identity bindings, and sync status in a local SQLite cache. Media file paths
and media file contents are not imported. Explicit feedback is stored separately
in the same application database; it does not change Plex.

When you invoke tools, their results are sent to your MCP client (for example
ChatGPT or Codex). Those clients and their providers apply their own data policies.
Results can include your username, server/media identifiers, viewing dates, and
personal opinions. OAuth providers process authentication; a tunnel/proxy provider
handles transport according to its own policies. Self-hosting does not mean tool
results stay on your computer after you send them to a hosted assistant.

## Credentials and logs

Plex and local bearer secrets are loaded from operator configuration and are not
stored in the cache. OAuth access tokens are validated in memory. Optional Windows
tunnel credentials are protected using DPAPI and passed to the tunnel process via
its environment. Diagnostic errors suppress provider details, but discovery and
live verification output contain private account/library information. Keep all
logs and diagnostics private and review them before sharing.

## Retention, correction, and removal

There is no automatic retention expiry. Removed catalog entries and previously
imported viewing events can remain in the cache; local feedback survives syncs.
Plex pruning or unwatching does not remove imported events. You can inspect,
correct, or delete individual feedback records through the authenticated tools
with explicit instructions. Stop the service and remove its private cache,
configuration, logs, and backups to erase the deployment's stored data. This does
not erase copies already sent to an MCP client/provider; manage those separately.

To disconnect, remove the client connection and revoke the relevant provider
grants/tokens; stop the bridge for immediate access removal. Do not publish real
account details, caches, credentials, or raw support bundles in this repository.
