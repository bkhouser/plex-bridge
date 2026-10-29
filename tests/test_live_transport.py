import asyncio
import socket
import threading
import time

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from plex_advisor.mcp_server import create_app


def test_official_client_real_loopback_listener(config, populated):
    """Real TCP MCP initialization, discovery, and invocation; fixtures only, no real Plex."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    port = listener.getsockname()[1]
    config = config.model_copy(update={"mcp_port": port})
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config, populated[0]),
            log_level="error",
            access_log=False,
            proxy_headers=False,
        )
    )
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started

        async def verify():
            async with httpx.AsyncClient(
                headers={"Authorization": "Bearer " + config.local_bearer_token.get_secret_value()}
            ) as http:
                async with streamable_http_client(
                    f"http://127.0.0.1:{port}/mcp", http_client=http
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert len(tools.tools) == 11
                        result = await session.call_tool(
                            "search_library",
                            {
                                "filters": {
                                    "genre": "Science Fiction",
                                    "year_min": 2010,
                                    "watched": False,
                                }
                            },
                        )
                        assert not result.isError
                        assert result.structuredContent["total_titles"] == 1
                        print(f"Verified official MCP client over TCP localhost:{port}: 11 tools")

        asyncio.run(verify())
    finally:
        server.should_exit = True
        worker.join(10)
        listener.close()
        assert not worker.is_alive()
