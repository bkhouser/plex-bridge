"""Verify the configured loopback MCP endpoint with read-only official-client calls."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from plex_advisor.config import Settings
from plex_advisor.models import utc_now
from plex_advisor.security import configure_logging


async def verify() -> dict[str, Any]:
    config = Settings()
    configure_logging("ERROR")
    config.require_auth()
    if config.auth_mode != "local":
        raise ValueError("This verifier requires local authentication")
    calls = [
        ("get_plex_status", {}),
        ("get_library_statistics", {}),
        (
            "search_library",
            {
                "filters": {
                    "genre": "Science Fiction",
                    "year_min": 2010,
                    "watched": False,
                    "limit": 3,
                }
            },
        ),
        (
            "search_library",
            {"filters": {"query": "Fargo", "year_min": 1996, "year_max": 1996, "limit": 5}},
        ),
        ("get_watch_history", {"filters": {"limit": 5}}),
        ("check_library_availability", {"titles": [{"title": "Ronin", "year": 1998}]}),
        ("get_taste_profile", {}),
    ]
    results = []
    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer " + config.local_bearer_token.get_secret_value()},
        trust_env=False,
        timeout=30,
    ) as http:
        endpoint = f"http://127.0.0.1:{config.mcp_port}/mcp"
        anonymous = await http.get(endpoint, headers={"Authorization": ""})
        if anonymous.status_code != 401:
            raise ValueError("Unauthenticated request was not rejected")
        async with streamable_http_client(endpoint, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments)
                    if result.isError or not result.structuredContent:
                        raise ValueError("MCP tool failed")
                    results.append(
                        {"tool": name, "arguments": arguments, "result": result.structuredContent}
                    )
    return {
        "checked_at": utc_now(),
        "unauthenticated_status": anonymous.status_code,
        "listed_tools": [t.name for t in listed.tools],
        "calls": results,
        "all_calls_passed": True,
    }


if __name__ == "__main__":
    try:
        report = asyncio.run(verify())
        destination = Path("data/live-mcp-verification.json")
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "all_calls_passed": report["all_calls_passed"],
                    "unauthenticated_status": report["unauthenticated_status"],
                    "tools_listed": len(report["listed_tools"]),
                    "successful_calls": [x["tool"] for x in report["calls"]],
                },
                indent=2,
            )
        )
    except Exception as exc:
        print(json.dumps({"error": "MCP verification failed", "category": type(exc).__name__}))
        sys.exit(1)
