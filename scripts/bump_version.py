"""Bump the project version in pyproject.toml and src/rutracker_proxy/__init__.py.

    python scripts/bump_version.py            # 0.1.1 -> 0.1.2 (patch)
    python scripts/bump_version.py minor      # 0.1.1 -> 0.2.0
    python scripts/bump_version.py --current  # print the current version only

Prints the resulting version. After the change is merged into main, the Release workflow tags it,
creates the GitHub release and publishes the images.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "rutracker_proxy" / "__init__.py"

_PYPROJECT_RE = re.compile(r'^version = "(\d+)\.(\d+)\.(\d+)"$', re.M)
_INIT_RE = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.M)


def current() -> str:
    match = _PYPROJECT_RE.search(PYPROJECT.read_text("utf-8"))
    if not match:
        raise ValueError(f"no version in {PYPROJECT}")
    init = _INIT_RE.search(INIT.read_text("utf-8"))
    if not init or init.groups() != match.groups():
        raise ValueError(f"{INIT} and {PYPROJECT} disagree on the version")
    return ".".join(match.groups())


def bumped(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown part {part!r}, expected major, minor or patch")


def write(version: str) -> None:
    for path, pattern, template in (
        (PYPROJECT, _PYPROJECT_RE, 'version = "{}"'),
        (INIT, _INIT_RE, '__version__ = "{}"'),
    ):
        text = path.read_text("utf-8")
        path.write_text(pattern.sub(template.format(version), text, count=1), "utf-8", newline="\n")


def main(argv: list[str]) -> int:
    try:
        version = current()
        if "--current" not in argv:
            version = bumped(version, argv[0] if argv else "patch")
            write(version)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
