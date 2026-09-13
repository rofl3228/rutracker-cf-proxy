"""Checks for definitions/rutracker-proxy.yml.

Prowlarr runs the title filters with .NET regex. The `regex` module supports the same
features used here (variable-length lookbehind, Unicode scripts), so the filters are
replayed in Python after translating .NET-only syntax.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import regex
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFINITION = ROOT / "definitions" / "rutracker-proxy.yml"


@pytest.fixture(scope="module")
def definition():
    return yaml.safe_load(DEFINITION.read_text("utf-8"))


def render_template(text: str, config: dict) -> str:
    """Minimal Go-template support: {{ if .Config.x }}A{{ else }}B{{ end }}."""
    pattern = regex.compile(r"\{\{ if \.Config\.(\w+) \}\}(.*?)\{\{ else \}\}(.*?)\{\{ end \}\}", regex.S)
    return pattern.sub(lambda m: m.group(2) if config[m.group(1)] else m.group(3), text)


def dotnet_to_python(pattern: str) -> str:
    return pattern.replace(r"\p{IsCyrillic}", r"\p{Cyrillic}")


def dotnet_replacement(replacement: str) -> str:
    return regex.sub(r"\$(\d)", r"\\g<\1>", replacement)


def apply_filters(filters: list, value: str, config: dict) -> str:
    for f in filters:
        name, args = f["name"], f.get("args")
        if name == "re_replace":
            pattern, replacement = args
            value = regex.sub(
                dotnet_to_python(pattern),
                dotnet_replacement(render_template(replacement, config)),
                value,
            )
        elif name == "trim":
            value = value.strip()
        else:
            raise NotImplementedError(name)
    return value


DEFAULTS = {"stripcyrillic": True, "addrus": True}

TITLES = [
    # movies
    ("Кэти уезжает / Katie Says Goodbye (Уэйн Робертс / Wayne Roberts) [2016, США, Франция, драма, WEB-DL 1080p] + Sub Rus, Eng + Original Eng",
     "Katie Says Goodbye 2016 [WEB-DL 1080p] + Sub Rus, Eng + Original Eng"),
    ("Детка на драйве / She Rides Shotgun (Ник Роулэнд / Nick Rowland) [2025, США, триллер, драма, BDRemux 1080p] MVO (Dragon Money Studio) + Dub, MVO Ukr + Sub (Ukr, Eng etc.) + Original Eng",
     "She Rides Shotgun 2025 [BDRemux 1080p] MVO (Dragon Money Studio) + Dub, MVO Ukr + Sub (Ukr, Eng etc.) + Original Eng RUS"),
    ("Заманчивое предложение / Дверь в подвал / Cellar Door (Вон Стайн / Vaughn Stein) [2024, США, ужасы, триллер, драма, детектив, WEB-DL 720p] Dub + VO + Original Eng + Sub Rus, Eng",
     "Cellar Door 2024 [WEB-DL 720p] Dub + VO + Original Eng + Sub Rus, Eng RUS"),
    ("Холоп 3 (Клим Шипенко) [2026, комедия, приключения, WEB-DLRip-AVC]",
     "Холоп 3 2026 [WEBRip-AVC]"),
    ("Ирония судьбы, или С лёгким паром! (Эльдар Рязанов) [1976, мелодрама, лирическая комедия, DVDRemux] Издание Крупный План/Lizard Digital Video, без реставрации",
     "Ирония судьбы, или С лёгким паром! 1976 [DVDRip] Издание Крупный План/Lizard Digital Video, без реставрации"),
    ("Похищение века (Виталий Макаров) [1981, Комедия, социальная сатира, DVDRip, AVC] [Sub RUS + ENG]",
     "Похищение века 1981 [DVDRip, AVC] [Sub RUS + ENG]"),
    ("Цветок найден! / Flower found! (Йорн Лииуверинк / Jorn Leeuwerink) [2017, Нидерланды, мультфильм для взрослых, сатира, WEB-DL 720p]",
     "Flower found! 2017 [WEB-DL 720p]"),
    ("Жихарка (Наталия Голованова) [1977, СССР, сказка, короткометражка, 35mm film scan Rip 1080p] Original Rus",
     "Жихарка 1977 [35mm film scan Rip 1080p] Original Rus"),
    # series
    ("Укрытие / Бункер / Silo / Сезон: 3 / Серии: 1-10 из 10 (Майкл Диннер, Арик Авелино) [2026, США, Фантастика, драма, триллер, WEB-DL 1080p] MVO (LostFilm) + Original + Sub (Rus, Eng)",
     "Silo S03 [WEB-DL 1080p] MVO (LostFilm) + Original + Sub (Rus, Eng) RUS"),
    ("Фауда / Fauda / פאודה / Сезон: 5 / Серии: 1-11 из 11 (Омри Гивон) [2026, Израиль, Драма, WEB-DL 1080p] MVO (NewStudio) + Original + Eng + Sub (Rus, Ukr, Heb, Eng)",
     "Fauda S05 [WEB-DL 1080p] MVO (NewStudio) + Original + Eng + Sub (Rus, Ukr, Heb, Eng) RUS"),
    ("Далеко до Москвы / Сезон: 1 / Серии: 1-12 из 32 (Андрей Хрулёв, Евгения Яцкина, Сергей Репецкий) [2026, детектив, криминал, WEBRip-AVC]",
     "Далеко до Москвы S01E01-E12 [WEBRip-AVC]"),
    ("Второе зрение / Сезон: 1,2 / Серии: 1-28 из 28 (Кирилл Белевич, Владимир Виноградов) [2016-2022, детектив, WEBRip-AVC] + Sub (Rus)",
     "Второе зрение S01-S02 [WEBRip-AVC] + Sub (Rus)"),
    ("Жених года / Серии: 1-4 из 4 (Анатолий Артамонов) [2026, Мелодрама, WEBRip]",
     "Жених года S01 [WEBRip]"),
    ("Звездный Путь - Фаза 2 (Неофициальный) - Китамб / Star Trek - Phase II (Fan series) - Kitumba / Сезон: 1 / Серии: 8 из 11 (Марк Скотт Зикри Вик Миньонья Рик Чемберс) [2013, США, Фантастика, HDTVRip 1080p] Original + Sub (Rus, Eng)",
     "Star Trek - Phase II (Fan series) - Kitumba S01E08 [HDTV 1080p] Original + Sub (Rus, Eng)"),
    ("Стражи Вселенной / Yu zhou hu wei dui / Cosmicrew / Сезон: 2-3 / Серии: 1-52 из 52 (Робин Го, Ким Чинён / Robin Goh, Kim Jinyeong) [2019-2020, Китай, мультфильм, фантастика, приключения, WEB-DL 1080p] Dub",
     "Yu zhou hu wei dui S02-S03 [WEB-DL 1080p] Dub RUS"),
]


@pytest.mark.parametrize("raw,expected", TITLES)
def test_title_filters(definition, raw, expected):
    filters = definition["search"]["fields"]["title"]["filters"]
    assert apply_filters(filters, raw, DEFAULTS) == expected


def test_title_filters_keep_russian_names_when_disabled(definition):
    filters = definition["search"]["fields"]["title"]["filters"]
    raw = TITLES[0][0]
    result = apply_filters(filters, raw, {"stripcyrillic": False, "addrus": False})
    assert result.startswith("Кэти уезжает / Katie Says Goodbye 2016 [WEB-DL 1080p]")


@pytest.mark.parametrize("query,expected", [
    ("Silo S03", "Silo"),
    ("Silo S03E05", "Silo"),
    ("Katie Says Goodbye 2016", "Katie Says Goodbye 2016"),
    ("Mission: Impossible - Fallout", "Mission Impossible Fallout"),
    ("Холоп 3", "Холоп 3"),
])
def test_keyword_filters(definition, query, expected):
    assert apply_filters(definition["search"]["keywordsfilters"], query, DEFAULTS) == expected


def test_required_schema_keys(definition):
    for key in ("id", "name", "description", "language", "type", "encoding", "links", "caps", "search"):
        assert key in definition
    fields = definition["search"]["fields"]
    for key in ("title", "size", "seeders", "category", "download"):
        assert key in fields
    assert definition["encoding"] == "windows-1251"


def test_category_mappings_are_generated_and_unique(definition):
    mappings = definition["caps"]["categorymappings"]
    ids = [str(m["id"]) for m in mappings]
    assert len(ids) > 1000
    assert len(ids) == len(set(ids))
    by_id = {str(m["id"]): m["cat"] for m in mappings}
    assert by_id["313"] == "Movies/HD"  # Зарубежное кино (HD Video)
    assert by_id["2366"] == "TV/HD"
    assert by_id["33"] == "TV/Anime"


def test_category_mappings_up_to_date():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_categories.py"), "--check"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
