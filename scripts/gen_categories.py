"""Generate caps.categorymappings for definitions/rutracker-proxy.yml.

Every RuTracker forum (from definitions/rutracker_forums.tsv) gets a Newznab category:
first by section, then overridden per root forum, then by keywords in the forum name.
Result rows carry the *subforum* id (a.f href), so all 1300 forums must be mapped,
otherwise results from unmapped forums would have no category and Radarr/Sonarr ignore them.

    python scripts/gen_categories.py            # rewrite the block inside the YAML
    python scripts/gen_categories.py --check    # exit 1 if the YAML is out of date (used by tests)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TSV = ROOT / "definitions" / "rutracker_forums.tsv"
YAML = ROOT / "definitions" / "rutracker-proxy.yml"
BEGIN = "    # BEGIN generated categorymappings (scripts/gen_categories.py)"
END = "    # END generated categorymappings"

# Section (optgroup) -> default category. None = skip (not releases).
SECTIONS = {
    "Новости": None,
    "Кино, Видео и ТВ": "Movies",
    "Сериалы": "TV",
    "Документалистика и юмор": "TV/Documentary",
    "Спорт": "TV/Sport",
    "Книги и журналы": "Books",
    "Обучение иностранным языкам": "Books/Foreign",
    "Обучающие видео": "Other",
    "Аудиокниги": "Audio/Audiobook",
    "Авто и мото": "Other",
    "Музыка": "Audio",
    "Популярная музыка": "Audio",
    "Джазовая и Блюзовая музыка": "Audio",
    "Рок-музыка": "Audio",
    "Электронная музыка": "Audio",
    "Hi-Res форматы, оцифровки": "Audio/Lossless",
    "Музыкальное видео": "Audio/Video",
    "Игры": "PC/Games",
    "Программы и Дизайн": "PC",
    "Мобильные устройства": "PC/Mobile-Other",
    "Apple": "PC/Mac",
    "Разное": "Other",
    "Обсуждения, встречи, общение": None,
    "Приватные форумы": None,
}

# Root forum id -> category for the root and all its subforums.
ROOTS = {
    "1289": None,  # Rutracker Awards
    "22": "Movies", "7": "Movies/Foreign", "124": "Movies/Other", "511": "Movies/Other",
    "93": "Movies/DVD", "2198": "Movies/HD", "718": "Movies/UHD", "352": "Movies/3D",
    "4": "Movies",  # мультфильмы: Radarr
    "921": "TV",  # мультсериалы: Sonarr
    "33": "TV/Anime",
    "9": "TV", "189": "TV/Foreign", "2366": "TV/HD", "119": "TV/UHD", "911": "TV/Foreign", "2100": "TV/Foreign",
    "19": "TV/Other", "670": "TV/Other",
    "314": "TV/Documentary", "24": "TV/Other",
    "1418": "Books/Technical", "862": "Books/Comics",
    "548": "Console", "2185": "Console", "650": "PC/Mobile-Other", "240": "Other",
    "899": "PC/Games", "960": "PC/Mac",
    "1376": "PC/ISO", "1012": "PC/ISO",
    "285": "PC/Mobile-Other", "957": "PC/Mobile-Other",
    "1933": "PC/Mobile-iOS", "2235": "Movies/Other", "2238": "Movies/HD", "2236": "Audio/Other",
    "1299": "Audio/Lossless",
}

# (regex on forum name, section filter, category) checked in order; first match wins.
KEYWORDS = [
    (r"UHD|2160p|4K", ("Movies", "TV"), {"Movies": "Movies/UHD", "TV": "TV/UHD"}),
    (r"HD Video|\bHD\b|1080|720", ("Movies", "TV"), {"Movies": "Movies/HD", "TV": "TV/HD"}),
    (r"DVD", ("Movies",), {"Movies": "Movies/DVD"}),
    (r"\b3D\b", ("Movies",), {"Movies": "Movies/3D"}),
    (r"Android", ("PC",), {"PC": "PC/Mobile-Android"}),
    (r"lossless|FLAC|Hi-Res", ("Audio",), {"Audio": "Audio/Lossless"}),
]


def load_forums():
    forums = []
    for line in TSV.read_text("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        fid, parent, section, name = line.split("\t")
        forums.append((fid, parent, section, name))
    return forums


def category_for(fid: str, parent: str, section: str, name: str) -> str | None:
    root = parent or fid
    if section not in SECTIONS:
        raise KeyError(f"unknown section {section!r} (forum {fid}); add it to SECTIONS")
    cat = SECTIONS[section]
    if root in ROOTS:
        cat = ROOTS[root]
    if cat is None:
        return None
    family = cat.split("/")[0]
    # Keyword refinement only for generic categories, so explicit root choices stay.
    if "/" not in cat or cat in ("Movies/Foreign", "TV/Foreign"):
        for pattern, families, mapping in KEYWORDS:
            if family in families and re.search(pattern, name, re.I):
                return mapping[family]
    return cat


def render() -> str:
    lines = [BEGIN]
    for fid, parent, section, name in load_forums():
        cat = category_for(fid, parent, section, name)
        if cat is None:
            continue
        desc = name.replace('"', "'")
        lines.append(f'    - {{id: {fid}, cat: {cat}, desc: "{desc}"}}')
    lines.append(END)
    return "\n".join(lines)


def main() -> int:
    text = YAML.read_text("utf-8")
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        print(f"markers not found in {YAML}")
        return 2
    updated = pattern.sub(lambda _: render(), text)
    if "--check" in sys.argv:
        if updated != text:
            print("categorymappings are out of date: run python scripts/gen_categories.py")
            return 1
        return 0
    YAML.write_text(updated, "utf-8", newline="\n")
    print(f"updated {YAML}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
