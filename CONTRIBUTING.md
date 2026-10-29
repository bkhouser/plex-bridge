# Contributing to Plex Bridge

Discuss substantial changes in an issue before implementing them. Keep the
project a focused bridge: factual Plex data for an MCP client, with optional
explicit local feedback. Plex writes, arbitrary HTTP/SQL/shell/filesystem tools,
implicit feedback collection, and cross-profile access are outside its scope.

Use Python 3.12+ and a virtual environment. Install `python -m pip install -e '.[dev]'`.
`requirements-tested.txt` records the release-tested environment and can be
used as constraints; CI tests current dependencies within the declared bounds.
Tests use synthetic data and must not require Plex, OAuth credentials, or a real cache.

Before submitting a pull request:

```sh
python -m ruff check plex_advisor tests scripts
python -m ruff format --check plex_advisor tests scripts
python -m mypy plex_advisor
python -m pytest -q
python -m pip_audit --skip-editable
python scripts/validate_plugin.py
python scripts/build_artifacts.py
python -m build
```

On Windows, also run `pwsh -NoProfile -File tests/test_startup.ps1`.
Describe the problem, change, validation, and any migration implications. Add
meaningful regression tests for defects. Preserve nulls, provenance, account
binding, stale-cache warnings, and confirmation requirements. Append migrations;
do not edit previously released schema definitions. Do not update dependencies
or broaden authentication/network permissions incidentally.

By contributing, you agree that your contribution is licensed under the MIT
license. Be respectful and constructive. Report security issues privately using
[SECURITY.md](SECURITY.md), not an ordinary issue.
