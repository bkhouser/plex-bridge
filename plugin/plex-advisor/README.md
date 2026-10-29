# Plex Bridge — local plugin template

An unofficial MCP bridge for personal Plex library information, ratings, and viewing history. Plex Bridge is an independent project and is not affiliated with, endorsed by, or sponsored by Plex.

Previously named Plex Advisor. The package identifier and existing `plex-advisor` commands are retained for compatibility.

Start the separately installed `plex-advisor serve` process. This package references its default local endpoint. In your MCP-capable host, configure the Authorization bearer header privately using the independent local MCP secret. The package deliberately includes no secret and cannot authenticate by itself. Each installation requires its own client secret configuration.

For remote ChatGPT use, first complete OAuth and the approved Secure MCP Tunnel or HTTPS connection. Create the connected plugin through ChatGPT's custom MCP flow. Do not upload this localhost URL expecting cloud access. See the application repository's `docs/chatgpt_setup.md`.

Keep confirmation enabled for `record_media_feedback` and `delete_media_feedback`. This skill does not grant consent to save opinions merely because they were mentioned in conversation.
