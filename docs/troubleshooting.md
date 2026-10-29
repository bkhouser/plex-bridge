# Troubleshooting

| Symptom | Check |
| --- | --- |
| Discovery fails | Privately verify token, Plex origin/allowed hostname, network reachability and TLS. Never paste a token into an issue. |
| Identity mismatch | Compare cloud ID/username, server identifier and local history ID/name with discovery and the intended Plex profile. Do not substitute an owner token to bypass a failure. |
| Cache belongs to another configuration | Use a new database for changed identities or library selections. Preserve existing feedback; do not edit the binding in SQL. |
| Sync is partial | Read the coarse section/history error and freshness. The previous failed section/history is preserved; successful sections may update. |
| History missing or excluded | Check explicit history enrollment and library membership. Plex history can be incomplete; `sync --full` cannot reconstruct evidence Plex no longer supplies. |
| Ratings differ | Distinguish personal `userRating` from critic/audience scores, confirm profile, and sync. Unknown public providers are not labeled critic scores. |
| Local MCP returns 401 | Use the separate local bearer in the client's private configuration. A Plex token is not an MCP credential. |
| OAuth returns 401 | Check issuer, audience/resource, RS256 access-token profile, exact subject, expiry and `library:read`. An ID token is not an access token. |
| Feedback denied | Authorize `feedback:write` and explicitly request the local write. Keep client confirmation enabled. |
| Tunnel startup refused | Run OAuth preflight; verify the generated profile's main endpoint, loopback ports and runtime-key scope. |
| Port already used | Choose another application port and update client/profile consistently. Never stop an unrelated process automatically. |
| Sandbox transport test stalls | Tests need local loopback sockets. Run in an environment permitting local networking; use a new writable pytest `--basetemp`. |

Report bugs using synthetic examples and the application/Python/OS versions.
Do not include `.env`, tokens, raw discovery results, real caches or private logs.

For `plex_api_incompatible` or `cache_unavailable_or_incompatible`, follow [compatibility and recovery](compatibility.md). Back up before changing dependencies or restoring a cache; never bypass the checks by editing schema versions or identity.
