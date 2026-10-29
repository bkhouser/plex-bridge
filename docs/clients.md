# Local clients and Codex

The server uses MCP Streamable HTTP at `http://127.0.0.1:8765/mcp` by default,
not stdio. Run `plex-advisor serve` in its configured working directory. The
client sends `Authorization: Bearer <MCP secret>` using the separate
`PA_LOCAL_BEARER_TOKEN`, never the Plex token.

Example private Codex user configuration:

```toml
[mcp_servers.plex_bridge]
url = "http://127.0.0.1:8765/mcp"
bearer_token_env_var = "PLEX_BRIDGE_MCP_TOKEN"
```

Set `PLEX_BRIDGE_MCP_TOKEN` in Codex's launch environment to the same private
secret configured for the server. Use a secret manager/private environment
mechanism, not a literal in this repository, shell arguments or chat. Reload the
client as needed. See current [Codex MCP documentation](https://developers.openai.com/codex/mcp/).
A client on another machine cannot reach your machine's localhost; use the
[authenticated remote setup](chatgpt_setup.md) instead.

The plugin package contains a skill and endpoint template; it neither launches
the service nor supplies credentials. Other clients supporting Streamable HTTP
and private bearer configuration can connect too. Verify `get_plex_status` and
compare a rating/history sample with Plex. Skill text does not grant consent to
save opinions; keep write confirmation enabled.
