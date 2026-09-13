from pathlib import Path

import pytest

from rutracker_proxy.config import Settings

ENV_VARS = ["PORT", "UPSTREAM_URL", "BYPARR_URL", "IP_FAMILY", "CLEARANCE_FILE", "CLEARANCE_MAX_AGE", "LOG_LEVEL"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    s = Settings.from_env()
    assert s.port == 8080
    assert s.upstream_url == "https://rutracker.org"
    assert s.ip_family == "4"
    assert s.impersonate == "firefox"
    assert s.clearance_file is None


def test_overrides(monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("UPSTREAM_URL", "https://rutracker.net/")
    monkeypatch.setenv("CLEARANCE_FILE", "/data/clearance.json")
    monkeypatch.setenv("CLEARANCE_MAX_AGE", "1800")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    s = Settings.from_env()
    assert s.port == 9000
    assert s.upstream_url == "https://rutracker.net"
    assert s.clearance_file == Path("/data/clearance.json")
    assert s.clearance_max_age == 1800
    assert s.log_level == "DEBUG"


@pytest.mark.parametrize("name,value", [("PORT", "abc"), ("IP_FAMILY", "5")])
def test_invalid_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings.from_env()
