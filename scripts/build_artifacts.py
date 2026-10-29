"""Build source/plugin archives from an explicit source allowlist; never include caches."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
dist = ROOT / "dist"
dist.mkdir(exist_ok=True)


def files_under(folder: str):
    for path in sorted((ROOT / folder).rglob("*")):
        if (
            path.is_file()
            and not path.is_symlink()
            and "__pycache__" not in path.parts
            and path.suffix
            in {".py", ".ps1", ".md", ".json", ".yml", ".yaml", ".service", ".example"}
        ):
            yield path


manifest = json.loads((ROOT / "plugin/plex-advisor/plugin.json").read_text())
assert len(manifest["extensions"]["com.openai"]["interface"]["shortDescription"]) <= 30
assert manifest["name"] == "plex-advisor"
with ZipFile(dist / "plex-bridge-plugin.zip", "w", ZIP_DEFLATED) as archive:
    for path in files_under("plugin/plex-advisor"):
        archive.write(path, path.relative_to(ROOT / "plugin"))

with ZipFile(dist / "plex-bridge-source.zip", "w", ZIP_DEFLATED) as archive:
    for folder in ["plex_advisor", "tests", "docs", "deploy", "scripts", "plugin", ".github"]:
        for path in files_under(folder):
            archive.write(path, Path("plex-advisor") / path.relative_to(ROOT))
    for filename in [
        "README.md",
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "pyproject.toml",
        ".env.example",
        ".gitignore",
        ".gitattributes",
        "requirements-tested.txt",
    ]:
        path = ROOT / filename
        if path.exists():
            archive.write(path, Path("plex-advisor") / filename)
for path in [dist / "plex-bridge-plugin.zip", dist / "plex-bridge-source.zip"]:
    with ZipFile(path) as archive:
        assert archive.testzip() is None
        assert not any(x.endswith("/.env") or ".sqlite" in x for x in archive.namelist())
    print(f"{path.name}: {path.stat().st_size} bytes; archive integrity verified")
