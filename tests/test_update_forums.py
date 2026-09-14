import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import update_forums  # noqa: E402


def payload(extra_forums: int = 0):
    """Shape of api.rutracker.cc/v1/static/cat_forum_tree, trimmed."""
    forums = {"7": "Зарубежное кино", "313": "Зарубежное кино (HD Video)", "189": "Зарубежные сериалы",
              "2366": "Зарубежные сериалы (HD Video)", "1608": "&#9917; Футбол", "25": "Доска почета!"}
    tree = {"2": {"7": [313]}, "18": {"189": [], "2366": []}, "28": {"1608": []}, "36": {"25": []}}
    for i in range(extra_forums):
        forums[str(10000 + i)] = f"Форум {i}"
        tree["2"]["7"].append(10000 + i)
    return {"result": {
        "c": {"2": "Кино, Видео и ТВ", "18": "Сериалы", "28": "Спорт", "36": "ОБХОД БЛОКИРОВОК"},
        "f": forums,
        "tree": tree,
    }}


def test_build_rows_keeps_site_order_and_unescapes_names():
    rows = update_forums.build_rows(payload())
    assert rows == [
        ("7", "", "Кино, Видео и ТВ", "Зарубежное кино"),
        ("313", "7", "Кино, Видео и ТВ", "Зарубежное кино (HD Video)"),
        ("189", "", "Сериалы", "Зарубежные сериалы"),
        ("2366", "", "Сериалы", "Зарубежные сериалы (HD Video)"),
        ("1608", "", "Спорт", "⚽ Футбол"),
        ("25", "", "ОБХОД БЛОКИРОВОК", "Доска почета!"),
    ]


def test_validate_accepts_full_tree(monkeypatch):
    monkeypatch.setattr(update_forums, "MIN_FORUMS", 5)
    update_forums.validate(update_forums.build_rows(payload()))


@pytest.mark.parametrize("bad,match", [
    ({"result": {}}, "only 0 forums"),
    ({"error": "rate limited"}, "only 0 forums"),
])
def test_validate_rejects_wrong_answers(bad, match):
    with pytest.raises(ValueError, match=match):
        update_forums.validate(update_forums.build_rows(bad))


def test_validate_requires_movie_and_tv_sections(monkeypatch):
    monkeypatch.setattr(update_forums, "MIN_FORUMS", 1)
    rows = [r for r in update_forums.build_rows(payload()) if r[2] != "Сериалы"]
    with pytest.raises(ValueError, match="Сериалы"):
        update_forums.validate(rows)


def test_diff_and_summary(tmp_path):
    tsv = tmp_path / "forums.tsv"
    update_forums.write_tsv(tsv, [
        ("7", "", "Кино, Видео и ТВ", "Зарубежное кино"),
        ("313", "", "Кино, Видео и ТВ", "Зарубежное кино HD"),   # renamed and moved under 7
        ("999", "", "Сериалы", "Удалённый форум"),               # removed
    ])
    old = update_forums.read_tsv(tsv)
    assert tsv.read_text("utf-8").startswith("# RuTracker forum tree")

    changes = update_forums.diff(old, update_forums.build_rows(payload()))
    assert [r[0] for r in changes["added"]] == ["189", "2366", "1608", "25"]
    assert [r[0] for r in changes["removed"]] == ["999"]
    assert [(o[3], n[3]) for o, n in changes["renamed"]] == [("Зарубежное кино HD", "Зарубежное кино (HD Video)")]
    assert [n[0] for _, n in changes["moved"]] == ["313"]

    text = update_forums.summary_markdown(changes)
    assert "| 2366 Зарубежные сериалы (HD Video) | Сериалы | TV/HD |" in text
    assert "| 25 Доска почета! | ОБХОД БЛОКИРОВОК | skipped |" in text
    assert "4 added, 1 removed, 1 renamed, 1 moved" in text


def test_no_changes_means_empty_diff(tmp_path):
    rows = update_forums.build_rows(payload())
    tsv = tmp_path / "forums.tsv"
    update_forums.write_tsv(tsv, rows)
    assert not any(update_forums.diff(update_forums.read_tsv(tsv), rows).values())


def test_all_api_sections_are_known_to_gen_categories():
    import gen_categories
    assert {r[2] for r in update_forums.build_rows(payload())} <= set(gen_categories.SECTIONS)
