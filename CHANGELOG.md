# Changelog

All notable Cloth NeXt changes. Versioning follows
[docs/RELEASE_POLICY.md](docs/RELEASE_POLICY.md); the canonical version lives in
`cloth_next/blender_manifest.toml`.

## 0.2.0-beta.1 — 2026-07-12 (beta channel)

### Added
- **Add-on update workflow** in the Add-on Preferences: installed version
  display, Stable/Beta update channel selection (defaults to Beta while a
  prerelease is installed), an explicit *Check for Updates* action against the
  official channel repositories, *Install Update* through Blender's own
  extension mechanism (`extensions.repo_sync` + `extensions.package_upgrade_all`),
  an *Add Channel Repository* action (explicit, duplicate-safe), *Open Blender
  Extensions* fallback, and *Open Release Notes*. Cloth NeXt never replaces its
  own extension files and never mixes add-on updates with PPF solver updates.
- Strict pure-Python version parser for the policy-supported forms
  (`X.Y.Z`, `X.Y.Z-beta.N`, `X.Y.Z-rc.N`) with correct beta/rc/stable ordering.
- Update install path enforces `addon_update_guard`: blocked in every
  application state that is not explicitly update-safe; stops only solver
  processes Cloth NeXt started itself and never touches external servers or
  the separately installed solver.
- **Phase 2.8A Physics Properties integration**: a native "Cloth NeXt" entry
  below Blender's Add Physics buttons, per-object enable/remove operators, a
  Cloth NeXt panel in Physics Properties with the object role (Cloth/Collider),
  and persistent per-object settings. No N-panel; simulation controls follow in
  the next phase.

### Fixed
- The *Download Official Solver* button did nothing: an operator subclass of a
  registered operator corrupted Blender's RNA↔Python mapping, silently skipping
  the confirmation dialog. The shared dialog behavior now lives in a plain
  mixin.
- Solver download now shows real progress in the preferences (with a
  reload-safe UI refresh timer), respects Blender's *Allow Online Access*
  setting, and surfaces installer errors visibly instead of failing silently.
- Blender smoke test runs reliably: source-tree fallback on Linux CI (the
  extension is Windows-only), `is_registered`-based assertions, and CI creates
  the build output directory.

### Notes
- The PPF Contact Solver remains external ST Tech / ZOZO software, installed
  separately after explicit confirmation; it is not part of this package.
- Add-on updates install exclusively through Blender's extension system from
  the policy-defined channel repositories; restart Blender after an update.

## 0.1.0 — 2026-07-12

- Initial release: pure core (state machine, errors, events, logging), PPF
  health check and process manager, verified solver bootstrap, managed solver
  installer with confirmation/SHA-256/health gates, release pipeline with
  Stable/Beta Blender extension repositories, Phase 2.7 hardening.
