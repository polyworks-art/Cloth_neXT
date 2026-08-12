# Solver compatibility manifest

`cloth_next/solver_compatibility.json` pins, per platform, the exact official
`st-tech/ppf-contact-solver` releases Cloth NeXt has verified. It contains
metadata only — never binary data. Validation lives in
`cloth_next/updater/solver_manifest.py`, `tools/validate_release_policy.py`,
and `tests/test_solver_compatibility_manifest.py`.

## Schema (manifest_version 2)

```json
{
  "manifest_version": 2,
  "cloth_next_version": "<must equal blender_manifest.toml version>",
  "platforms": {
    "windows-x86_64": {
      "default_release_id": "ppf-0.13-stable",
      "releases": [
        {
          "id": "ppf-0.13-stable",
          "codename": "Velune",
          "display_name": "Velune",
          "channel": "stable",
          "solver_package_version": "0.1.0",
          "protocol_version": "0.13",
          "schema_version": "2",
          "official_repository": "st-tech/ppf-contact-solver",
          "official_release_tag": "2026-07-26-22-53",
          "official_asset_name": "ppf-contact-solver-2026-07-26-22-53-win64.zip",
          "official_asset_url": "https://github.com/st-tech/ppf-contact-solver/releases/download/<tag>/<asset>",
          "download_size": 448046043,
          "sha256": "<64 lowercase hex>",
          "archive_layout_version": 1,
          "health_check_required": true
        }
      ]
    }
  }
}
```

## Release names

`codename` is Cloth NeXt's product-facing name for one tested solver generation.
It does not rename, fork, modify, or claim ownership of the external solver.
The immutable upstream identity remains the official repository, release tag,
asset name, asset hash, protocol, and schema.

Current names:

- **Velune** — protocol `0.13`, schema `2`
- **Lumen** — protocol `0.18`, schema `2`

A codename stays attached to its protocol generation. Small rebuilds and
compatible fixes keep the same codename; a new codename requires a deliberate,
reviewed compatibility change. Existing registries with older date-based names
are presented with the verified codename without changing executable paths or
release identity.

## Rules

- Only official `st-tech/ppf-contact-solver` release assets; the URL must be
  exactly `https://github.com/st-tech/ppf-contact-solver/releases/download/<tag>/<asset>`.
- No Cloth NeXt mirrors, unofficial forks, arbitrary CI artifacts, invented
  URLs, local paths, or mutable `latest` references.
- SHA-256, protocol version, and schema version are mandatory.
- Placeholder values (`VERIFIED_…` etc.) fail validation; while no verified
  source exists, automatic download stays disabled and the UI offers only
  "Select Existing Installation" and "Open Official Download Page".
- Unknown solver versions are never assigned a codename or assumed compatible.
  A new upstream release is offered only after it has been tested and added in
  a reviewed change.

## Release identity

The pair `official_release_tag` + `sha256` is the immutable identity of a
verified release. Managed installations store this identity in `current.json`
and compare it against the manifest to decide whether an update is available;
`solver_package_version` is a compatibility check of the downloaded executable,
never a sufficient release identity. Different official releases may report the
same internal package version.

Changing the `sha256` of an already published `official_release_tag` is an
integrity problem, not a silent release switch. The codename is presentation
metadata and never participates in executable verification or compatibility.
