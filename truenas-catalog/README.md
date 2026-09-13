# TrueNAS Apps catalog entry

`rutracker-cf-proxy/` is the app for the community train of [truenas/apps](https://github.com/truenas/apps),
in the layout of `ix-dev/community/<app>/`. The vendored library (`templates/library/`) is not stored here:
it is generated in the catalog repository.

## Submitting

1. Create the release the app points to: git tag `v0.1.0` → CI publishes `kirfeo/rutracker-cf-proxy:0.1.0`,
   and a GitHub release `v0.1.0` so `changelog_url` lists the shipped version.
2. Open an issue in truenas/apps proposing the app (their PR template asks for it).
3. Fork truenas/apps, copy this folder to `ix-dev/community/rutracker-cf-proxy/`.
4. In the fork, vendor the library and fill `lib_version_hash` (bump `lib_version` to the newest `library/2.x.x` first):
   ```bash
   devbox run copy-lib
   ```
5. Run the catalog tests (needs Docker):
   ```bash
   ./.github/scripts/ci.py --app rutracker-cf-proxy --train community --test-file basic-values.yaml
   ./.github/scripts/ci.py --app rutracker-cf-proxy --train community --test-file prowlarr-definition-values.yaml
   ./.github/scripts/port_validation.py
   ./.github/scripts/generate_metadata.py --app rutracker-cf-proxy --train community
   ```
   `generate_metadata.py` bumps `version` when it changes metadata; keep `1.0.0` for the first submission.
6. Open the PR using [PR.md](PR.md) as the description and attach an icon (PNG, square).

Both test files passed with library 2.3.11 on 2026-09-14 (rendered, deployed, healthy; the definition helper
wrote the file and exited 0). The default port 30490 was free in the catalog at that time.

Note: running `ci.py` directly on a TrueNAS host needs `/var/run/middleware` mounted into the render container
and test paths outside the read-only `/opt`; on a regular Docker host it works as is.
