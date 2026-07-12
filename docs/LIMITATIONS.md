# Audit limitations and unsupported claims

- The Cloth NeXt repository contained no implementation to execute or compare.
- Upstream was audited at commit `7193f158` on 2026-07-12. Protocol/schema and docs can
  change; implementation must pin a compatible solver release.
- No NVIDIA solver binary was installed or launched in this audit. Findings are static
  source/documentation evidence, not a successful GPU solve on this workstation.
- Phase 2.5 now has a locally bundled official solver and the real health integration
  test passes. The 1.43 GB extracted runtime remains untracked local state.
- Blender 5.x is not installed or discoverable through `PATH` or the standard
  `C:\Program Files\Blender Foundation` location on this machine. The Phase-1 Blender
  registration smoke test therefore cannot run here; this concrete environment cause
  was recorded before Phase 2 began.
- PPF 0.11 status responses do not carry schema or package versions. Full verification
  is possible for an owned local executable via `--version`; external servers remain
  protocol-identified but schema-unverified.
- The official release tag commit (`4f42d8c1…`) is earlier than the audited source
  commit (`7193f158…`). Runtime compatibility is verified as package `0.1.0`, protocol
  `0.11`, schema `1`; source identity is not falsely claimed.
- The official Windows archive contained no root project license. Bootstrap used the
  unchanged Apache-2.0 license from the official checkout and preserved discovered
  third-party notices. Formal release notice review remains open.
- Publishing the 453 MB solver-inclusive extension has not been authorized. Git LFS is
  installed but unconfigured; the local solver is ignored under strategy B.
- Exact CBOR scene schemas are extensive. This audit records the envelope and relevant
  keys, but implementation requires upstream format definitions and golden fixtures;
  filenames named `.pickle` must not be mistaken for Python pickle content.
- PPF supports incremental complete-frame fetching. It does not promise real-time
  delivery or a stable frame cadence; UI wording remains Buffered Live/Follow Solver.
- PC2 is proven by the official client for constant topology, not yet selected through
  a Cloth NeXt integration test. It cannot represent topology-changing tearing.
- Pressure is verified as a uniform shell parameter. Target volume, compressibility,
  gas behavior and pressure animation are not yet verified and must not be exposed.
- Independent Blender-style self-collision controls and tension/compression/shear
  stiffness mappings are not established. PPF contact is unified; fake mappings are
  prohibited.
- Weighted/animated pin details require schema fixtures before UI design.
- Dynamic tearing/ripping is unsupported by the unchanged solver. Only disabled future
  interfaces may exist.
- The official add-on's release index updates that add-on. No supported standalone
  solver stable/experimental manifest with checksums was found, so automatic solver
  updates cannot be implemented safely yet.
- The official add-on is a technical reference only. Cloth NeXt will not copy it in
  full or import it at runtime.
- Retaining the repository's GPL-3.0 `LICENSE` is a legal/project decision, not a
  conclusion that all future distribution and bundled solver licensing is resolved.

## Required evidence before implementation claims support

For every PPF feature, record upstream commit/release, protocol/schema versions, source
definition, a serialized fixture, observed server response, and an integration test.
If any item is missing, present the feature as unsupported or experimental rather than
guessing an endpoint, parameter or format.
- The Phase 2.6 remote beta end-to-end test has not run yet: it needs a
  user-chosen `-beta.N` version, pushed code and tag, GitHub Pages configured to
  serve the `gh-pages` branch, and a local Blender 5.x installation for the
  install/update verification. A stable release is forbidden until it passes.
- The release workflow pins `BLENDER_VERSION` for the official extension
  tooling; the exact Blender 5.x download URL must be confirmed on first CI run.
- The solver compatibility manifest pins exactly one verified upstream release
  (`2026-07-09-04-39`). Newer upstream releases are not offered until tested
  and added through a reviewed manifest change.
- The preferences UI code paths that require Blender (operators, dialogs) are
  covered only by the pure view-model tests on this machine, because Blender
  5.x is not installed locally.
- The Phase 2.7 Blender registration smoke test is wired into CI
  (`.github/workflows/ci.yml`, job `blender-smoke`) but has not run on this
  machine because Blender 5.x is not installed locally. The pinned CI Blender
  download URL and the `extension install-file` invocation must be confirmed on
  the first CI run.
- The `BUILDING` state exists in the state machine and wire-status mapping, but
  no code issues real `build` requests yet; that is Phase 3 scope.
- Phase 2.8A Add-Physics placement: Blender's `PHYSICS_PT_add` panel draws its
  native Add-Physics buttons inside an internal two-column `grid_flow` that is
  not exposed to appended draw callbacks. The "Cloth NeXt" entry therefore
  renders as a full-width native-style button directly below the native button
  grid, added through the stable `Panel.append`/`Panel.remove` API. Placing it
  inside the grid (e.g. beside a FLIP-Fluids button) would require replacing or
  monkey-patching Blender's `PHYSICS_PT_add.draw`, which is deliberately not
  done. The `MOD_CLOTH` icon is a temporary stand-in for a Cloth NeXt icon.
