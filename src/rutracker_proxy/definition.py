"""Installing the bundled Cardigann definition into Prowlarr's Definitions/Custom folder.

Run as a one-shot container next to the proxy (see deploy/truenas-app.yaml):
- `links` is set to the address Prowlarr uses to reach the proxy (PUBLIC_URL);
  the previous default address goes to `legacylinks`, so indexers added with it follow along.
- The file is written only when its content changes, atomically.
- If PROWLARR_URL and PROWLARR_API_KEY are set and the file changed, Prowlarr is asked to
  reload definitions (IndexerDefinitionUpdate command), so no restart is needed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

log = logging.getLogger(__name__)

FILENAME = "rutracker-proxy.yml"
DEFAULT_LINK = "http://127.0.0.1:30240/"

_LINKS_BLOCK = re.compile(r"^links:\n(?:  - .*\n)+", re.M)


@dataclass(frozen=True)
class InstallResult:
    path: Path
    changed: bool
    reloaded: bool


def bundled_definition() -> str:
    return resources.files("rutracker_proxy").joinpath("definitions", FILENAME).read_text("utf-8")


def normalize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://[^/]+", url):
        raise ValueError(f"PUBLIC_URL must look like http://host:port/, got {url!r}")
    return url if url.endswith("/") else url + "/"


def render(template: str, public_url: str) -> str:
    """Point `links` at `public_url`; keep the old default as a legacy link."""
    public_url = normalize_url(public_url)
    block = f"links:\n  - {public_url}\n"
    if public_url != DEFAULT_LINK:
        block += f"legacylinks:\n  - {DEFAULT_LINK}\n"
    rendered, count = _LINKS_BLOCK.subn(block, template, count=1)
    if count != 1:
        raise ValueError("bundled definition has no `links:` block")
    return rendered


def install(target_dir: Path, public_url: str) -> tuple[Path, bool]:
    """Write the definition into `target_dir` if it differs. Returns (path, changed)."""
    if not target_dir.is_dir():
        raise FileNotFoundError(f"definition folder {target_dir} does not exist (is Prowlarr's Definitions/Custom mounted?)")
    content = render(bundled_definition(), public_url)
    path = target_dir / FILENAME
    try:
        if path.read_text("utf-8") == content:
            return path, False
    except FileNotFoundError:
        pass
    tmp = path.with_name(f".{FILENAME}.tmp")
    tmp.write_text(content, "utf-8", newline="\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return path, True


def reload_prowlarr(prowlarr_url: str, api_key: str, timeout: float = 30) -> None:
    """Ask Prowlarr to re-read indexer definitions (clears its definition cache)."""
    url = prowlarr_url.rstrip("/") + "/api/v1/command"
    request = urllib.request.Request(
        url,
        data=json.dumps({"name": "IndexerDefinitionUpdate"}).encode(),
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status >= 300:
            raise RuntimeError(f"Prowlarr answered HTTP {response.status}")


def run(target_dir: Path, public_url: str, prowlarr_url: str | None, api_key: str | None) -> InstallResult:
    path, changed = install(target_dir, public_url)
    if not changed:
        log.info("definition %s is up to date", path)
        return InstallResult(path, changed=False, reloaded=False)
    log.info("definition written to %s (links -> %s)", path, normalize_url(public_url))

    if not (prowlarr_url and api_key):
        log.info("PROWLARR_URL/PROWLARR_API_KEY not set: changes apply after Prowlarr restarts")
        return InstallResult(path, changed=True, reloaded=False)
    try:
        reload_prowlarr(prowlarr_url, api_key)
    except (urllib.error.URLError, OSError, RuntimeError) as e:
        # The file is in place; a failed reload must not fail the app deployment.
        log.warning("could not ask Prowlarr to reload definitions: %s (changes apply after its restart)", e)
        return InstallResult(path, changed=True, reloaded=False)
    log.info("Prowlarr asked to reload indexer definitions")
    return InstallResult(path, changed=True, reloaded=True)
