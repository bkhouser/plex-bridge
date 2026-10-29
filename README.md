# Plex Bridge

**Your Plex library, connected.** An unofficial, self-hosted MCP bridge that gives
ChatGPT, Codex, and other MCP clients factual access to your personal Plex library,
ratings, and viewing history. Your assistant handles conversation and recommendations.

Plex Bridge is an independent project and is **not affiliated with, endorsed by,
or sponsored by Plex**. It is not an approved OpenAI directory listing.

## What it does

- Searches selected movie and TV libraries with exact IDs and pagination.
- Separates your personal ratings from critic and audience ratings.
- Reads the explicitly enrolled profile's history and watched/resume state.
- Reports cache freshness, incomplete history, and uncertain data explicitly.
- Provides optional factual movie candidates and taste summaries for your assistant.
- Saves qualitative feedback locally only when explicitly requested.

**Plex is read-only.** No library administration, playback control, downloading,
Plex database access, Tautulli, scraping, or arbitrary HTTP/SQL/shell tools.
Use one instance/database per profile. There is no hosted multi-tenant service,
standalone web UI, extra model API, embedding service, or vector database.

## Requirements

Python 3.12+, a reachable Plex Media Server, an authorized Plex token, and a
compatible MCP client. Some managed/shared account tokens cannot independently
verify their identity and are deliberately rejected. History depends on server
permissions and retention. See [profile enrollment](docs/configuration.md).

## Quick start

Windows PowerShell:

```powershell
git clone https://github.com/bkhouser/plex-bridge.git
Set-Location plex-bridge
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
Copy-Item .env.example .env
```

Linux/macOS:

```sh
git clone https://github.com/bkhouser/plex-bridge.git
cd plex-bridge
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
cp .env.example .env
chmod 600 .env
```

Edit `.env` privately: set the Plex origin, exact allowed hostname and Plex token.
Never paste credentials into chat, URLs, GitHub issues, or shell arguments.
Activate your virtual environment or use the full executable path for these commands:

```text
plex-advisor discover
```

Discovery prints private enrollment information. Set the exact server identifier,
cloud account ID/username, and selected library IDs. For history, independently
verify the local history account ID/name and explicitly confirm the binding;
cloud and local IDs are not interchangeable. Generate a separate random MCP
bearer secret of at least 32 characters with a trusted password manager and
store it privately as `PA_LOCAL_BEARER_TOKEN`.

```text
plex-advisor diagnose
plex-advisor sync --full
plex-advisor serve
```

Compare diagnostic samples with the intended profile in Plex before trusting the
data. Default endpoint: `http://127.0.0.1:8765/mcp`. In a second terminal, run
`plex-advisor smoke` to verify local authentication through the official MCP SDK.

`serve` refreshes at startup and every four hours. `serve --no-scheduler` serves
an existing cache. `sync --full` reconciles older retrievable history. An outage
preserves cached data with a warning. Opening a database can apply schema
migrations; back up before upgrades.

## Connect your assistant

- [Local MCP clients and Codex](docs/clients.md): private bearer configuration;
  the included plugin ZIP is a local template and does not start the server.
- [ChatGPT and remote clients](docs/chatgpt_setup.md): OAuth plus an approved
  Secure MCP Tunnel or HTTPS deployment. Never forward local bearer mode to
  the internet. Publishing this repository does not host a service.

Try: “Use Plex Bridge to show my recent viewing history and personal ratings.”
Or: “Find unwatched movies under 100 minutes in my library.” Unwatched means
current Plex state; it does not prove you have never seen a title.

## Documentation

The [tool reference](docs/tools.md) covers all eleven tools. All require
authentication; remote feedback writes also need `feedback:write`. Configure
client confirmation for writes. The assistant makes recommendation judgments.

- [Configuration](docs/configuration.md) / [Architecture](docs/architecture.md)
- [Deployment and backup](docs/deployment.md) / [Windows startup](docs/windows-startup.md)
- [Privacy](docs/privacy.md) / [Security policy](SECURITY.md)
- [Troubleshooting](docs/troubleshooting.md) / [Limitations](docs/limitations.md)
- [Plex/API/cache compatibility and recovery](docs/compatibility.md)
- [Upgrades](docs/cache-upgrade-v2.md) / [Historical recovery](docs/historical-recovery.md)
- [Verification](docs/verification.md) / [Release review](docs/release-review.md)
- [Contributing](CONTRIBUTING.md) / [Changelog](CHANGELOG.md) / [Research](docs/research.md)

## Compatibility and license

Formerly Plex Advisor. Existing `plex-advisor` commands, `plex_advisor` imports,
`PA_` settings, package identifiers, storage paths and service names are retained
for compatibility. The public project is named Plex Bridge.

MIT licensed; see [LICENSE](LICENSE). Early self-hosted release, with no hosted
service or support SLA. Review security boundaries before exposing an endpoint.
