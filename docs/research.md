# Source and reuse research

Checked 2026-10-11. Existing open-source candidates were examined before implementation:

| Project | Fit decision |
|---|---|
| [niavasha/plex-mcp-server](https://github.com/niavasha/plex-mcp-server) | Broad Plex, Sonarr, Radarr and Trakt integration; outside this narrowly read-only Python service |
| [sandraschi/plexmcp](https://github.com/sandraschi/plexmcp) | Broader desktop/web, playback and RAG feature surface than requested |
| [Python-PlexAPI](https://github.com/pushingkarmaorg/python-plexapi) | Reused as the Plex client dependency; tested installed version 4.18.3 |
| [Official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk) | Reused server, authentication middleware and client; tested version 1.30.0 |

No third-party application's source was copied. The project composes the established libraries with a small read-only adapter and a cache tailored to the requested account and feedback boundaries.

Current primary references:

- [Plex Media Server API](https://developer.plex.tv/pms/) (large reference page could not be fully fetched by the web reader; installed SDK source and endpoint documentation were also inspected).
- [Python-PlexAPI server/account/history reference](https://python-plexapi.readthedocs.io/en/latest/modules/server.html).
- [Build an MCP server](https://developers.openai.com/plugins/build/mcp-server).
- [OpenAI authentication guidance](https://developers.openai.com/plugins/build/auth).
- [ChatGPT connection and evaluation](https://developers.openai.com/plugins/deploy/connect-chatgpt).
- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).
- [MCP authorization specification](https://modelcontextprotocol.io/specification/latest/basic/authorization).
- [Agent Plugins MCP schema](https://agent-plugins.org/schemas/1.0.0/mcp.schema.json).

A bounded GPT-6.1 Sol review examined account isolation. Its key result was adopted: separate token-subject verification from explicit operator enrollment of local history identity; check both on every connection. Missing switch tokens are rejected before any client can fall back to global credentials.
