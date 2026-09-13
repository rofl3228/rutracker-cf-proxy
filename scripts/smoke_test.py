"""End-to-end smoke test through a running proxy, behaving like Prowlarr's Cardigann login.

Uses only the standard library. The cookie jar enforces Domain/Secure rules like .NET does,
so a wrongly rewritten Set-Cookie shows up as "not logged in".

    LOGIN=... PASSWORD=... python scripts/smoke_test.py http://127.0.0.1:30240

Credentials come from the environment and are never printed. One login attempt only.
"""

from __future__ import annotations

import http.cookiejar
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:30240").rstrip("/")
    login, password = os.environ.get("LOGIN"), os.environ.get("PASSWORD")
    if not login or not password:
        print("LOGIN/PASSWORD not set")
        return 2

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)
    failures = 0

    def request(method: str, path: str, data: bytes | None = None, headers: dict | None = None):
        req = urllib.request.Request(base + path, data=data, method=method, headers=headers or {})
        started = time.monotonic()
        try:
            resp = opener.open(req, timeout=180)
        except urllib.error.HTTPError as e:  # 3xx/4xx/5xx still carry a response
            resp = e
        body = resp.read()
        print(f"{method} {path} -> {resp.status} {len(body)}B {time.monotonic() - started:.2f}s "
              f"ct={resp.headers.get('Content-Type')} loc={resp.headers.get('Location')}")
        return resp, body

    def check(name: str, ok: bool) -> None:
        nonlocal failures
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        failures += not ok

    resp, body = request("GET", "/health")
    check("health reachable", resp.status == 200)

    resp, body = request("GET", "/forum/login.php")
    check("login page is rutracker, not Cloudflare", resp.status == 200 and b"login_username" in body)

    form = urllib.parse.urlencode(
        {"login_username": login, "login_password": password, "login": "Вход", "redirect": "index.php"},
        encoding="cp1251",
    ).encode("ascii")
    resp, body = request("POST", "/forum/login.php", form, {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": base + "/forum/login.php",
    })
    if b"cap_sid" in body:
        print("CAPTCHA requested by rutracker: stop and do not retry for a while")
        return 1
    check("login redirects to a proxy-relative index", resp.status == 302 and resp.headers.get("Location") == "/forum/index.php")
    check("bb_session stored by the cookie jar", any(c.name == "bb_session" for c in jar))
    check("no Cloudflare cookies leaked to client", not any(c.name.startswith(("cf_", "__cf")) for c in jar))

    resp, body = request("GET", "/forum/index.php")
    check("logged in (id=\"logged-in-username\")", b'id="logged-in-username"' in body)

    query = urllib.parse.urlencode({"nm": "Матрица"}, encoding="cp1251")
    resp, body = request("GET", f"/forum/tracker.php?{query}")
    topics = re.findall(rb"dl\.php\?t=(\d+)", body)
    check("cyrillic search returns results", resp.status == 200 and len(topics) > 0)
    check("charset is windows-1251", "1251" in (resp.headers.get("Content-Type") or ""))
    check("no absolute rutracker links left in HTML", not re.search(rb"https?://(www\.)?rutracker\.org/", body))
    try:
        body.decode("cp1251")
        check("body decodes as cp1251", True)
    except UnicodeDecodeError:
        check("body decodes as cp1251", False)

    if topics:
        resp, body = request("GET", f"/forum/dl.php?t={topics[0].decode()}")
        check(".torrent downloaded", resp.status == 200 and body[:1] == b"d" and b"announce" in body[:200])

    print(f"\n{'ALL PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
