import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import bump_version  # noqa: E402


@pytest.fixture
def project(tmp_path, monkeypatch):
    pyproject = tmp_path / "pyproject.toml"
    init = tmp_path / "__init__.py"
    pyproject.write_text('[project]\nname = "x"\nversion = "0.1.1"\nrequires-python = ">=3.12"\n', "utf-8")
    init.write_text('"""doc"""\n\n__version__ = "0.1.1"\n', "utf-8")
    monkeypatch.setattr(bump_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(bump_version, "INIT", init)
    return pyproject, init


@pytest.mark.parametrize("part,expected", [("patch", "0.1.2"), ("minor", "0.2.0"), ("major", "1.0.0")])
def test_bump_updates_both_files(project, capsys, part, expected):
    pyproject, init = project
    assert bump_version.main([part]) == 0
    assert capsys.readouterr().out.strip() == expected
    assert f'version = "{expected}"' in pyproject.read_text("utf-8")
    assert f'__version__ = "{expected}"' in init.read_text("utf-8")
    assert 'requires-python = ">=3.12"' in pyproject.read_text("utf-8")


def test_current_only(project, capsys):
    assert bump_version.main(["--current"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.1"


def test_mismatch_is_an_error(project):
    _, init = project
    init.write_text('__version__ = "0.1.0"\n', "utf-8")
    assert bump_version.main([]) == 1


def test_repository_versions_agree():
    assert bump_version.current()
