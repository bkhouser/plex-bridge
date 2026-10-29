"""Validate plugin package manifests against their official schemas (network required)."""

import json
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator

root = Path(__file__).resolve().parents[1] / "plugin/plex-advisor"
for filename in ["plugin.json", "mcp.json"]:
    document = json.loads((root / filename).read_text())
    response = httpx.get(document["$schema"], timeout=20, follow_redirects=False)
    response.raise_for_status()
    validator = Draft202012Validator(response.json())
    validator.validate(document)
    print(f"{filename}: official schema validation passed")
