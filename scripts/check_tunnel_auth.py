"""Fail closed unless the actual tunnel target advertises configured OAuth protection."""

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests
from mcp.server.auth.routes import build_resource_metadata_url
from pydantic import AnyHttpUrl

from plex_advisor.config import Settings
from plex_advisor.security import configure_logging


def check(profile: Path) -> None:
    config = Settings()
    configure_logging("ERROR")
    config.require_auth()
    if config.auth_mode != "oauth":
        raise ValueError("OAuth is required")
    # The isolated profile uses JSON syntax, a valid subset of YAML.
    document = json.loads(profile.read_text(encoding="utf-8"))
    targets = document["mcp"]["server_urls"]
    expected = f"http://127.0.0.1:{config.mcp_port}/mcp"
    if len(targets) != 1 or targets[0] != {"channel": "main", "url": expected}:
        raise ValueError("Unexpected target")
    metadata_path = urlsplit(
        str(build_resource_metadata_url(AnyHttpUrl(config.oauth_resource)))
    ).path
    with requests.Session() as session:
        session.trust_env = False
        probe = session.get(expected, timeout=5, allow_redirects=False)
        if probe.status_code != 401:
            raise ValueError("Anonymous access was not denied")
        metadata = session.get(
            f"http://127.0.0.1:{config.mcp_port}{metadata_path}",
            timeout=5,
            allow_redirects=False,
        )
        if metadata.status_code != 200:
            raise ValueError("OAuth discovery unavailable")
        data = metadata.json()
        if data.get("resource") != config.oauth_resource or data.get("authorization_servers") != [
            config.oauth_issuer
        ]:
            raise ValueError("OAuth discovery mismatch")
    print("Running MCP target requires OAuth and advertises the configured identity provider.")


if __name__ == "__main__":
    try:
        check(Path(sys.argv[1]))
    except Exception:
        print("Tunnel not started: configure OAuth and restart Plex Bridge, then retry.")
        sys.exit(1)
