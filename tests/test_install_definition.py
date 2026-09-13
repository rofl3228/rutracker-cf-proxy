import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import yaml

from rutracker_proxy import definition


def test_bundled_definition_is_packaged():
    assert "id: rutracker-proxy" in definition.bundled_definition()


def test_render_default_link_has_no_legacy_links():
    data = yaml.safe_load(definition.render(definition.bundled_definition(), "http://127.0.0.1:30240"))
    assert data["links"] == ["http://127.0.0.1:30240/"]
    assert "legacylinks" not in data


def test_render_custom_link_keeps_old_default_as_legacy():
    text = definition.render(definition.bundled_definition(), "http://truenas.local:30240/")
    data = yaml.safe_load(text)
    assert data["links"] == ["http://truenas.local:30240/"]
    assert data["legacylinks"] == ["http://127.0.0.1:30240/"]
    assert data["caps"]["categorymappings"]  # rest of the file untouched
    assert text.count("links:") == 2  # links + legacylinks, nothing duplicated


@pytest.mark.parametrize("bad", ["truenas.local:30240", "ftp://x/", ""])
def test_render_rejects_bad_url(bad):
    with pytest.raises(ValueError):
        definition.render(definition.bundled_definition(), bad)


def test_install_writes_once_then_is_idempotent(tmp_path):
    path, changed = definition.install(tmp_path, "http://truenas.local:30240/")
    assert changed and path == tmp_path / "rutracker-proxy.yml"
    assert "http://truenas.local:30240/" in path.read_text("utf-8")

    _, changed_again = definition.install(tmp_path, "http://truenas.local:30240/")
    assert not changed_again

    _, changed_url = definition.install(tmp_path, "http://10.0.0.5:30240/")
    assert changed_url
    assert not list(tmp_path.glob(".*.tmp"))


def test_install_overwrites_manual_edits(tmp_path):
    (tmp_path / "rutracker-proxy.yml").write_text("edited by hand", "utf-8")
    _, changed = definition.install(tmp_path, "http://127.0.0.1:30240/")
    assert changed
    assert (tmp_path / "rutracker-proxy.yml").read_text("utf-8").startswith("---")


def test_install_requires_existing_folder(tmp_path):
    with pytest.raises(FileNotFoundError, match="Definitions/Custom"):
        definition.install(tmp_path / "missing", "http://127.0.0.1:30240/")


@pytest.fixture
def fake_prowlarr():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        status = 201

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append({"path": self.path, "key": self.headers.get("X-Api-Key"), "body": json.loads(body)})
            self.send_response(401 if self.headers.get("X-Api-Key") != "secret" else 201)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", calls
    server.shutdown()


def test_run_reloads_prowlarr_only_when_changed(tmp_path, fake_prowlarr):
    url, calls = fake_prowlarr
    first = definition.run(tmp_path, "http://truenas.local:30240/", url, "secret")
    second = definition.run(tmp_path, "http://truenas.local:30240/", url, "secret")
    assert (first.changed, first.reloaded) == (True, True)
    assert (second.changed, second.reloaded) == (False, False)
    assert calls == [{"path": "/api/v1/command", "key": "secret", "body": {"name": "IndexerDefinitionUpdate"}}]


def test_run_without_api_key_just_writes(tmp_path, fake_prowlarr):
    url, calls = fake_prowlarr
    result = definition.run(tmp_path, "http://truenas.local:30240/", url, None)
    assert (result.changed, result.reloaded) == (True, False)
    assert calls == []


def test_run_survives_rejected_reload(tmp_path, fake_prowlarr):
    url, calls = fake_prowlarr
    result = definition.run(tmp_path, "http://truenas.local:30240/", url, "wrong-key")
    assert (result.changed, result.reloaded) == (True, False)
    assert (tmp_path / "rutracker-proxy.yml").exists()


def test_run_survives_unreachable_prowlarr(tmp_path):
    result = definition.run(tmp_path, "http://truenas.local:30240/", "http://127.0.0.1:1", "secret")
    assert (result.changed, result.reloaded) == (True, False)


def test_cli_exit_codes(tmp_path):
    ok = subprocess.run(
        [sys.executable, "-m", "rutracker_proxy", "install-definition"],
        env={"DEFINITION_DIR": str(tmp_path), "PUBLIC_URL": "http://truenas.local:30240", "SYSTEMROOT": "C:\\Windows", "PATH": ""},
        capture_output=True, text=True,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "definition written" in ok.stdout

    missing = subprocess.run(
        [sys.executable, "-m", "rutracker_proxy", "install-definition"],
        env={"DEFINITION_DIR": str(tmp_path / "nope"), "SYSTEMROOT": "C:\\Windows", "PATH": ""},
        capture_output=True, text=True,
    )
    assert missing.returncode == 1
    assert "definition not installed" in missing.stdout
