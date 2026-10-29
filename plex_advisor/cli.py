import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import threading
from typing import Any

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

from plex_advisor.config import Settings
from plex_advisor.database import Database
from plex_advisor.errors import (
    CACHE_COMPATIBILITY_HELP,
    PLEX_COMPATIBILITY_HELP,
    CacheCompatibilityError,
    PlexCompatibilityError,
)
from plex_advisor.mcp_server import create_app
from plex_advisor.plex_client import PlexSource
from plex_advisor.security import configure_logging
from plex_advisor.synchronization import bind_configuration, synchronize


def refresh_loop(config: Settings, stop: threading.Event, *, immediate: bool) -> None:
    if not immediate and stop.wait(config.sync_interval_seconds):
        return
    while not stop.is_set():
        try:
            db = Database(config.database_path)
            outcome = synchronize(db, config, PlexSource(config))
            logging.getLogger("plex_advisor").info("Refresh status: %s", outcome["status"])
            if outcome.get("compatibility_action"):
                logging.getLogger("plex_advisor").error(PLEX_COMPATIBILITY_HELP)
        except (CacheCompatibilityError, sqlite3.DatabaseError):
            logging.getLogger("plex_advisor").error(CACHE_COMPATIBILITY_HELP)
        except Exception:
            logging.getLogger("plex_advisor").error("Refresh failed; cached data preserved")
        if stop.wait(config.sync_interval_seconds):
            break


async def smoke(config: Settings) -> dict[str, Any]:
    """Official MCP client; no Plex connection or local preference writes."""
    config.require_auth()
    if config.auth_mode != "local":
        raise ValueError("Use an OAuth-capable MCP client for production smoke tests")
    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer " + config.local_bearer_token.get_secret_value()},
        trust_env=False,
    ) as http:
        async with streamable_http_client(
            f"http://127.0.0.1:{config.mcp_port}/mcp", http_client=http
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                status = await session.call_tool("get_plex_status", {})
                return {
                    "tools": [t.name for t in tools.tools],
                    "status": status.structuredContent,
                    "is_error": status.isError,
                }


def main() -> None:
    parser = argparse.ArgumentParser(description="Unofficial Plex Bridge service and diagnostics")
    parser.add_argument(
        "command", choices=["discover", "diagnose", "sync", "status", "serve", "schedule", "smoke"]
    )
    parser.add_argument("--full", action="store_true", help="Reconcile all retrievable history")
    parser.add_argument(
        "--no-scheduler", action="store_true", help="Serve cache without periodic Plex refresh"
    )
    args = parser.parse_args()
    try:
        config = Settings()
        configure_logging(config.log_level)
        if args.command in {"discover", "diagnose"}:
            source = PlexSource(config)
            output = source.discover() if args.command == "discover" else source.diagnose()
        elif args.command == "smoke":
            output = asyncio.run(smoke(config))
        else:
            db = Database(config.database_path)
            bind_configuration(db, config)
            if args.command == "sync":
                config.require_plex()
                output = synchronize(db, config, PlexSource(config), full=args.full)
            elif args.command == "status":
                output = db.status(config.sync_interval_seconds)
            elif args.command == "schedule":
                config.require_plex()
                refresh_loop(config, threading.Event(), immediate=True)
                return
            else:
                app = create_app(config, db)
                stop = threading.Event()
                if not args.no_scheduler:
                    config.require_plex()
                    worker = threading.Thread(
                        target=refresh_loop,
                        args=(config, stop),
                        kwargs={"immediate": True},
                        daemon=True,
                    )
                    worker.start()
                try:
                    uvicorn.run(
                        app,
                        host=config.mcp_host,
                        port=config.mcp_port,
                        access_log=False,
                        proxy_headers=False,
                        log_level="warning",
                    )
                finally:
                    stop.set()
                return
        print(json.dumps(output, indent=2))
        if args.command == "sync" and output["status"] != "success":
            sys.exit(1)
    except PlexCompatibilityError:
        print(
            json.dumps({"error": "plex_api_incompatible", "action": PLEX_COMPATIBILITY_HELP}),
            file=sys.stderr,
        )
        sys.exit(3)
    except (CacheCompatibilityError, sqlite3.DatabaseError):
        print(
            json.dumps(
                {"error": "cache_unavailable_or_incompatible", "action": CACHE_COMPATIBILITY_HELP}
            ),
            file=sys.stderr,
        )
        sys.exit(3)
    except ValidationError as exc:
        # Pydantic's default message can print raw input, including credentials.
        fields = [".".join(str(x) for x in e["loc"]) or "configuration" for e in exc.errors()]
        print(json.dumps({"error": "Invalid configuration", "fields": fields}), file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        pass
    except Exception:
        print(
            json.dumps(
                {
                    "error": "Operation failed. Check configuration, identity, credentials and "
                    "connectivity. Provider details suppressed to protect secrets."
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
