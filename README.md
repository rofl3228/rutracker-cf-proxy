<img src="assets/icon.svg" alt="" width="96" align="right">

# rutracker-cf-proxy

**English** | [Русский](README_RU.md)

[![Test & publish image](https://github.com/rofl3228/rutracker-cf-proxy/actions/workflows/docker.yml/badge.svg)](https://github.com/rofl3228/rutracker-cf-proxy/actions/workflows/docker.yml)

A proxy between Prowlarr and rutracker.org that gets past Cloudflare protection.
The `cf_clearance` cookie comes from [byparr](https://github.com/ThePhaseless/Byparr); every request is sent by the proxy itself, impersonating the same browser (`curl_cffi`).
It ships a Cardigann indexer definition for Prowlarr that rewrites release titles into a format Radarr and Sonarr understand.

Image (linux/amd64, linux/arm64): [`ghcr.io/rofl3228/rutracker-cf-proxy`](https://github.com/rofl3228/rutracker-cf-proxy/pkgs/container/rutracker-cf-proxy) or [`kirfeo/rutracker-cf-proxy`](https://hub.docker.com/r/kirfeo/rutracker-cf-proxy), with the same tags.

## Why

Prowlarr's built-in FlareSolverr/byparr integration solves the challenge in a browser, then repeats the request with its own .NET client and the cookie it got.
Cloudflare rejects that replay: `cf_clearance` is bound to the User-Agent, the browser's TLS fingerprint and the IP address. On top of that, byparr cannot send POST requests, so logging in to RuTracker through it is impossible.

This proxy takes only the cookie and the User-Agent from byparr, and sends the actual requests (login, search, `.torrent` download) through `curl_cffi` with the same browser fingerprint, from the same IPv4 address.

Only search results (`tracker.php`) with at least one release are cached, separately for each RuTracker session. Login, the login check and `.torrent` downloads always go to the site. Responses carry an `X-Cache: HIT/MISS/SHARED` header; statistics are in `/health`.

Prowlarr talks to the proxy like a regular site: `http://127.0.0.1:30240/forum/tracker.php?...` is forwarded to `https://rutracker.org/forum/tracker.php?...`.
The proxy rewrites `Location` and `Set-Cookie` so the RuTracker session lives in Prowlarr, while the Cloudflare cookie stays inside the proxy.

## How it works

| File | Purpose |
|---|---|
| `src/rutracker_proxy/config.py` | settings from environment variables |
| `src/rutracker_proxy/challenge.py` | detects the Cloudflare "Just a moment..." page |
| `src/rutracker_proxy/byparr.py` | asks byparr to solve the challenge, takes `cf_clearance` and the User-Agent |
| `src/rutracker_proxy/clearance.py` | keeps the clearance, refreshes it once for all waiting requests, persists it to `/data` |
| `src/rutracker_proxy/upstream.py` | request to RuTracker; on a challenge refreshes the clearance and retries once |
| `src/rutracker_proxy/passthrough.py` | proxy route: rewrites headers, cookies and links, returns 502/503/504 errors |
| `src/rutracker_proxy/cache.py` | 5-minute search cache and de-duplication of identical concurrent requests |
| `src/rutracker_proxy/app.py` | HTTP server, `/health` |
| `src/rutracker_proxy/definition.py` | `install-definition` command: puts the definition into Prowlarr and asks it to reload |
| `src/rutracker_proxy/definitions/rutracker-proxy.yml` | Cardigann indexer definition |
| `scripts/smoke_test.py` | end-to-end check through a running proxy: login, search, `.torrent` |

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8080` | proxy port (`30240` in the TrueNAS app) |
| `BYPARR_URL` | `http://127.0.0.1:30230/v1` | byparr address |
| `UPSTREAM_URL` | `https://rutracker.org` | RuTracker mirror |
| `CLEARANCE_URL` | `https://rutracker.org/forum/login.php` | page byparr opens |
| `IMPERSONATE` | `firefox` | browser to impersonate; must match byparr's browser |
| `IP_FAMILY` | `4` | `4` / `6` / `any`; the cookie is bound to the IP, byparr uses IPv4 |
| `UPSTREAM_TIMEOUT` | `90` | RuTracker request timeout, seconds |
| `ORIGIN_RETRIES` | `1` | GET retries on Cloudflare 520–524 errors (RuTracker server did not respond); POST is never retried |
| `UPSTREAM_CONCURRENCY` | `2` | concurrent requests to RuTracker |
| `CACHE_TTL` | `300` | how long to keep search results, seconds (`0` disables the cache) |
| `CACHE_MAX_ENTRIES` | `100` | how many search results to keep in memory |
| `BYPARR_TIMEOUT` | `120` | how long byparr may take to solve the challenge, seconds |
| `CLEARANCE_MAX_AGE` | `0` | refresh the clearance ahead of time once it is older than N seconds (`0` means only on a challenge) |
| `CLEARANCE_FILE` | `/data/clearance.json` in Docker | where the clearance is kept across restarts |
| `LOG_LEVEL` | `INFO` | `DEBUG` logs every request |

## Running on TrueNAS

Apps → Discover Apps → Custom App → Install via YAML, paste [deploy/truenas-app.yaml](deploy/truenas-app.yaml) (adjust `TZ`, the byparr address and the data path; the data directory must be writable by uid 568).
The proxy and byparr must reach the internet from the same public IP, which is the case when they run on the same host.

Check:

```bash
curl http://127.0.0.1:30240/health
```

Update: Apps → rutracker-proxy → Update / Redeploy (pulls the latest `latest`).

Diagnostics: pass Cloudflare once and exit:

```bash
sudo docker run --rm --network host kirfeo/rutracker-cf-proxy check /forum/tracker.php?nm=test
```

`OK: ... -> 302 ... location=.../login.php` means Cloudflare was passed (a 302 to the login page is the normal answer for a guest).

End-to-end check through a running proxy (login and password from `.env`, a single login attempt):

```bash
sudo docker run --rm --network host --env-file .env -v $PWD/scripts:/scripts --entrypoint python kirfeo/rutracker-cf-proxy /scripts/smoke_test.py http://127.0.0.1:30240
```

## Connecting to Prowlarr

The indexer definition is bundled in the image; the one-shot `prowlarr-definition` service from [deploy/truenas-app.yaml](deploy/truenas-app.yaml) puts it into Prowlarr. On every start or update of the app it:
1. writes `rutracker-proxy.yml` into Prowlarr's `Definitions/Custom/` (only if the content changed; manual edits to the file are overwritten);
2. sets `links` to the `PUBLIC_URL` address; the previous default address goes to `legacylinks`, so Prowlarr switches existing indexers to the new one by itself;
3. if `PROWLARR_URL` and `PROWLARR_API_KEY` are set, asks Prowlarr to reload definitions (the `IndexerDefinitionUpdate` command), so no restart is needed. Without the key, changes apply after Prowlarr restarts;
4. exits.

The `Definitions/Custom` folder must exist and be writable by uid 568. The same thing by hand: `docker run --rm -v <folder>:/definitions -e PUBLIC_URL=... kirfeo/rutracker-cf-proxy install-definition`.

Adding the indexer:
1. Prowlarr → Indexers → Add Indexer → find **RuTracker (proxy)**.
2. Enter your RuTracker login and password. Do **not** assign a FlareSolverr tag.
3. Test → Save.

The definition source is `src/rutracker_proxy/definitions/rutracker-proxy.yml`. Categories are generated from `definitions/rutracker_forums.tsv`: after changing `scripts/gen_categories.py`, run `python scripts/gen_categories.py`.

The forum list follows RuTracker's public forum tree API. `python scripts/update_forums.py` refreshes the TSV and the categories and prints what changed; the [Update RuTracker forum list](.github/workflows/update-forums.yml) workflow runs it every Monday (or manually) and opens a pull request when something changed. Review the categories of added forums in the PR description. For the workflow to open PRs, enable Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests".

| `install-definition` variable | Default | Meaning |
|---|---|---|
| `DEFINITION_DIR` | `/definitions` | where to put the definition |
| `PUBLIC_URL` | `http://127.0.0.1:30240/` | proxy address as seen by Prowlarr |
| `PROWLARR_URL` | — | Prowlarr address for reloading definitions |
| `PROWLARR_API_KEY` | — | Prowlarr API key (Settings → General) |

## Building the image

GitHub Actions ([.github/workflows/docker.yml](.github/workflows/docker.yml)): tests on every push and pull request, then a build for amd64/arm64.
Images are published to Docker Hub and GHCR on pushes to `main` (tag `latest`) and on `vX.Y.Z` tags (tags `X.Y.Z` and `X.Y`); every build also gets a `sha-<commit>` tag.

Required repository secrets: `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (Docker Hub → Account settings → Personal access tokens, Read & Write). GHCR uses the built-in `GITHUB_TOKEN`.

## Development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m pytest
```
