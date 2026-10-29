import json

import pytest
from starlette.testclient import TestClient

from plex_advisor.mcp_server import create_app


@pytest.fixture
def client(config, populated):
    headers = {
        "Authorization": "Bearer " + config.local_bearer_token.get_secret_value(),
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(
        create_app(config, populated[0]), base_url="http://localhost:8765", headers=headers
    ) as c:
        yield c


def rpc(client, method, params=None):
    response = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    )
    assert response.status_code == 200, response.text
    return response.json()


def call(client, name, arguments=None):
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})["result"]
    assert not result.get("isError"), result
    return result["structuredContent"]


def test_initialize_schemas_annotations(client):
    initialized = rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "acceptance", "version": "1"},
        },
    )
    assert initialized["result"]["serverInfo"]["name"] == "Plex Bridge"
    tools = rpc(client, "tools/list")["result"]["tools"]
    assert len(tools) == 11
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["annotations"]["readOnlyHint"] == (
            tool["name"] not in {"record_media_feedback", "delete_media_feedback"}
        )


@pytest.mark.parametrize(
    ("name", "arguments", "key"),
    [
        ("get_plex_status", {}, "capabilities"),
        (
            "search_library",
            {"filters": {"genre": "Science Fiction", "year_min": 2010, "watched": False}},
            "items",
        ),
        ("get_media_details", {"media_id": "server-A:2"}, "personal_rating"),
        (
            "get_watch_history",
            {"filters": {"start": "2026-09-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"}},
            "events",
        ),
        ("get_library_statistics", {}, "movie_copies"),
        (
            "get_recommendation_candidates",
            {
                "request": {
                    "filters": {"genre": "Action", "watched": False, "max_runtime": 120},
                    "limit": 5,
                }
            },
            "candidates",
        ),
        ("check_library_availability", {"titles": [{"title": "Ronin"}]}, "results"),
        ("get_taste_profile", {}, "sample_sizes"),
        ("get_media_feedback", {}, "feedback"),
    ],
)
def test_read_tool_acceptance(client, name, arguments, key):
    result = call(client, name, arguments)
    assert key in result
    assert result["freshness"]["stale"] is False
    assert "PRIVATE-DO-NOT-LEAK" not in json.dumps(result)


def test_write_update_delete_tools(client):
    feedback = {
        "media_id": "server-A:1",
        "comments": "Enjoyable, rushed exposition",
        "sentiment": "mixed",
        "authorized": True,
    }
    created = call(client, "record_media_feedback", {"feedback": feedback})
    feedback["feedback_id"] = created["feedback_id"]
    feedback["comments"] = "Correction: confusing introduction"
    call(client, "record_media_feedback", {"feedback": feedback})
    saved = call(client, "get_media_feedback", {"media_id": "server-A:1"})
    assert saved["feedback"][0]["comments"] == feedback["comments"]
    assert call(
        client, "delete_media_feedback", {"feedback_id": created["feedback_id"], "authorized": True}
    )["deleted"]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("search_library", {"filters": {"limit": 100000}}),
        ("get_watch_history", {"filters": {"account_id": 999}}),
        ("record_media_feedback", {"feedback": {"media_id": "server-A:1", "comments": "casual"}}),
        ("delete_media_feedback", {"feedback_id": 1, "authorized": False}),
        ("get_media_details", {"media_id": "missing"}),
    ],
)
def test_invalid_arguments(client, name, arguments):
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    assert result.get("error") or result["result"].get("isError")


def test_cached_outage_tool(client, populated, config):
    from plex_advisor.synchronization import synchronize

    db, source = populated
    source.offline = True
    synchronize(db, config, source)
    result = call(client, "search_library", {"filters": {}})
    assert result["items"]
    assert result["freshness"]["warning"]
