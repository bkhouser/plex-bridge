"""Check the Git index for forbidden private artifacts and obvious credential patterns.

This is a bounded publication guard, not a complete secret detector or security audit.
Reports file names/line numbers only, never matched values.
"""

import re
import subprocess
from pathlib import PurePosixPath

FORBIDDEN_PARTS = {
    "data",
    "dist",
    ".venv",
    ".release-venv",
    ".audit-venv",
    ".test-tmp",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".release-tmp",
}
PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,}"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(rb"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}"),
]


def check() -> None:
    names = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    failures = []
    count = 0
    for name in filter(None, names):
        count += 1
        path = PurePosixPath(name)
        if (
            FORBIDDEN_PARTS.intersection(path.parts)
            or (path.name.startswith(".env") and name != ".env.example")
            or any(x in path.name for x in [".sqlite", ".dpapi"])
            or path.suffix in {".log", ".pem", ".key", ".pfx", ".p12", ".pyc"}
        ):
            failures.append(f"Forbidden artifact: {name}")
            continue
        entry = subprocess.check_output(["git", "ls-files", "--stage", "--", name])
        if entry.startswith(b"120000 "):
            failures.append(f"Symlink not allowed in release: {name}")
            continue
        content = subprocess.check_output(["git", "show", f":{name}"])
        for number, line in enumerate(content.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                failures.append(f"Potential credential (redacted): {name}:{number}")
    if failures:
        raise SystemExit("\n".join(failures))
    if not count:
        raise SystemExit("No staged/tracked files to check")
    print(f"Publication guard passed for {count} indexed files (not an exhaustive secret audit).")


if __name__ == "__main__":
    check()
