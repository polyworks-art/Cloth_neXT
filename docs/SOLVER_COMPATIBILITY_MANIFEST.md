# Solver compatibility manifest

`cloth_next/solver_compatibility.json` pins, per platform, the exact official
`st-tech/ppf-contact-solver` release Cloth NeXt has verified. It contains
metadata only — never binary data. Validated by
`cloth_next/updater/solver_manifest.py`, `tools/validate_release_policy.py`,
and `tests/test_solver_compatibility_manifest.py`.

## Schema (manifest_version 1)

```json
{
  "manifest_version": 1,
  "cloth_next_version": "<must equal blender_manifest.toml version>",
  "platforms": {
    "windows-x86_64": {
      "solver_package_version": "0.1.0",
      "protocol_version": "0.11",
      "schema_version": "1",
      "official_repository": "st-tech/ppf-contact-solver",
      "official_release_tag": "2026-07-09-04-39",
      "official_asset_name": "ppf-contact-solver-2026-07-09-04-39-win64.zip",
      "official_asset_url": "https://github.com/st-tech/ppf-contact-solver/releases/download/<tag>/<asset>",
      "download_size": 450996388,
      "sha256": "<64 lowercase hex>",
      "archive_layout_version": 1,
      "health_check_required": true
    }
  }
}
```

## Rules

- Only official `st-tech/ppf-contact-solver` release assets; the URL must be
  exactly `https://github.com/st-tech/ppf-contact-solver/releases/download/<tag>/<asset>`.
- No Cloth NeXt mirrors, unofficial forks, arbitrary CI artifacts, invented
  URLs, local paths, or mutable `latest` references.
- SHA-256, protocol version, and schema version are mandatory.
- Placeholder values (`VERIFIED_…` etc.) fail validation; while no verified
  source exists, automatic download stays disabled and the UI offers only
  "Select Existing Installation" and "Open Official Download Page".
- Unknown solver versions are never assumed compatible. A new upstream release
  is offered only after it has been tested and added here in a reviewed change.

## Release identity

The pair `official_release_tag` + `sha256` is the immutable identity of a
verified release. Managed installations store this identity in `current.json`
and compare it against the manifest to decide whether an update is available;
`solver_package_version` is a compatibility check of the downloaded
executable, never a sufficient release identity (different official releases
may report the same internal package version). Changing the `sha256` of an
already published `official_release_tag` is an integrity/manifest problem —
published official releases are immutable — and is logged as such by the
update check instead of being treated as a silent release switch.

## Current verified entry

The `windows-x86_64` entry pins official release tag `2026-07-09-04-39`
(package `0.1.0`, protocol `0.11`, schema `1`), whose asset hash and health
check were verified during the Phase 2.5 bootstrap (see
[SOLVER_DISTRIBUTION.md](SOLVER_DISTRIBUTION.md)).
