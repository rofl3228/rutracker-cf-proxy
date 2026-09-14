"""Refresh definitions/rutracker_forums.tsv from RuTracker's public forum tree API and regenerate categories.

The API (no login, not behind the Cloudflare challenge) lists every forum, including discussion and
archive subforums. They are kept on purpose: a forum without releases is harmless in a search's f[] list,
while guessing "non-release" forums by name would drop real ones.

    python scripts/update_forums.py [--summary summary.md]

Exit codes: 0 = done (changed or not), 2 = API answer looks wrong, 3 = new top-level section that
scripts/gen_categories.py does not know yet (add it to SECTIONS).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_categories  # noqa: E402

API_URL = os.environ.get("RUTRACKER_FORUM_TREE_URL", "https://api.rutracker.cc/v1/static/cat_forum_tree")
HEADER = "# RuTracker forum tree (id, parent id, section, name), from api.rutracker.cc cat_forum_tree\n"
# Sanity checks against an answer that is not the forum tree (error page, truncated JSON).
REQUIRED_SECTIONS = ("Кино, Видео и ТВ", "Сериалы")
MIN_FORUMS = 1000


def fetch_tree(url: str = API_URL) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "rutracker-cf-proxy forum updater"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def build_rows(payload: dict) -> list[tuple[str, str, str, str]]:
    """API payload -> [(forum id, parent id or "", section name, forum name)] in site order."""
    result = payload.get("result") or {}
    sections, forums, tree = result.get("c") or {}, result.get("f") or {}, result.get("tree") or {}
    rows = []
    for section_id, roots in tree.items():
        section = html.unescape(sections.get(section_id, ""))
        for root_id, children in roots.items():
            rows.append((str(root_id), "", section, html.unescape(forums.get(str(root_id), ""))))
            for child_id in children:
                rows.append((str(child_id), str(root_id), section, html.unescape(forums.get(str(child_id), ""))))
    return rows


def validate(rows: list[tuple[str, str, str, str]]) -> None:
    if len(rows) < MIN_FORUMS:
        raise ValueError(f"only {len(rows)} forums in the API answer, expected at least {MIN_FORUMS}")
    present = {row[2] for row in rows}
    missing = [s for s in REQUIRED_SECTIONS if s not in present]
    if missing:
        raise ValueError(f"sections missing from the API answer: {missing}")
    if any(not row[3] or "\t" in row[3] or "\n" in row[3] for row in rows):
        raise ValueError("some forum names are empty or contain tabs/newlines")


def read_tsv(path: Path) -> dict[str, tuple[str, str, str, str]]:
    if not path.exists():
        return {}
    rows = {}
    for line in path.read_text("utf-8").splitlines():
        if line and not line.startswith("#"):
            fid, parent, section, name = line.split("\t")
            rows[fid] = (fid, parent, section, name)
    return rows


def write_tsv(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    path.write_text(HEADER + "".join("\t".join(row) + "\n" for row in rows), "utf-8", newline="\n")


def diff(old: dict, new_rows: list[tuple[str, str, str, str]]) -> dict[str, list]:
    new = {row[0]: row for row in new_rows}
    return {
        "added": [new[f] for f in new if f not in old],
        "removed": [old[f] for f in old if f not in new],
        "renamed": [(old[f], new[f]) for f in new if f in old and old[f][3] != new[f][3]],
        "moved": [(old[f], new[f]) for f in new if f in old and old[f][1:3] != new[f][1:3]],
    }


def category(row: tuple[str, str, str, str]) -> str:
    return gen_categories.category_for(*row) or "skipped"


def summary_markdown(changes: dict[str, list]) -> str:
    lines = ["Automated refresh of the RuTracker forum list from `api.rutracker.cc`.", ""]
    counts = ", ".join(f"{len(v)} {k}" for k, v in changes.items())
    lines += [f"**Changes:** {counts}.", ""]
    if changes["added"]:
        lines += ["### Added", "", "| Forum | Section | Category |", "|---|---|---|"]
        lines += [f"| {r[0]} {r[3]} | {r[2]} | {category(r)} |" for r in changes["added"]]
        lines.append("")
    if changes["removed"]:
        lines += ["### Removed", "", "| Forum | Section |", "|---|---|"]
        lines += [f"| {r[0]} {r[3]} | {r[2]} |" for r in changes["removed"]]
        lines.append("")
    if changes["renamed"]:
        lines += ["### Renamed", "", "| Forum | Old name | New name |", "|---|---|---|"]
        lines += [f"| {o[0]} | {o[3]} | {n[3]} |" for o, n in changes["renamed"]]
        lines.append("")
    if changes["moved"]:
        lines += ["### Moved", "", "| Forum | Old parent / section | New parent / section | Category |", "|---|---|---|---|"]
        lines += [f"| {n[0]} {n[3]} | {o[1] or '-'} / {o[2]} | {n[1] or '-'} / {n[2]} | {category(n)} |" for o, n in changes["moved"]]
        lines.append("")
    lines += ["Check the categories above; adjust `scripts/gen_categories.py` if a forum landed in the wrong one."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--summary", type=Path, help="write a Markdown summary of the changes here")
    args = parser.parse_args()

    try:
        rows = build_rows(fetch_tree())
        validate(rows)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"forum tree not updated: {e}", file=sys.stderr)
        return 2

    unknown = sorted({row[2] for row in rows} - set(gen_categories.SECTIONS))
    if unknown:
        print(f"new sections, add them to SECTIONS in scripts/gen_categories.py: {unknown}", file=sys.stderr)
        return 3

    changes = diff(read_tsv(gen_categories.TSV), rows)
    changed = any(changes.values())
    if changed:
        write_tsv(gen_categories.TSV, rows)
        gen_categories.YAML.write_text(
            gen_categories.render_yaml(gen_categories.YAML.read_text("utf-8")), "utf-8", newline="\n"
        )

    text = summary_markdown(changes)
    print(text)
    if args.summary:
        args.summary.write_text(text, "utf-8")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
