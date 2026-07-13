# Cloth NeXt 0.3.0-dev.2 — experimental Dev test build

## Streaming cache update

The next Dev snapshot replaces the memory-heavy playback-cache post-process
with incremental PC2 streaming. Large bakes no longer retain or duplicate the
complete animation in Python memory; cache files and sidecars publish
atomically, cancellation preserves the previous valid result, and the UI
reports real cache-frame and finalization progress. No solver or quality
parameter changed.

This Dev-only snapshot is for practical Blender 5.1.2 testing. It adds guarded
no-downgrade update handoff, object-local uniform Pressure, and scene-wide
Solver Quality (`dt`, Newton, and PCG controls). Pressure and all quality values
participate in bake metadata and stale detection. `dt` defaults to `0.001` to
preserve Cloth NeXt's established behavior; Newton/PCG defaults come from the
pinned official PPF source at commit `7193f158`. No solver values were invented.

It is not a Beta or Stable release. Back up scenes and caches before testing.

## Previous beta notes

# Cloth NeXt 0.3.0-beta.1

This beta advances the real PPF workflow from the initial vertical slice to a
production-facing Bake flow with arbitrary frame ranges, cache-safe Rebake,
material controls, vertex-group Pinning, and a reusable Bake companion.

## Production-facing Bake workflow

Cloth NeXt can now:

- bake one Cloth NeXt cloth object against one static collider
- use artist-selected Bake ranges instead of a fixed eight-frame slice
- preserve the previous valid cache until replacement startup succeeds
- Rebake without Cloth NeXt's own Mesh Cache causing false topology errors
- use static or animated hard Pin targets from a Blender vertex group
- encode the documented PPF material, damping, and collision parameters
- show synchronized progress in Physics Properties, the HUD, and the companion
- cancel and clean up Cloth NeXt-owned solver sessions

## Stability and release hardening

- Developer Tools fail closed outside explicitly prepared Dev snapshots.
- The normal Pytest command excludes tests that require an explicit built ZIP;
  release workflows still run those artifact tests against the real candidate.
- Dev publishing accepts policy-valid release lines and retains builds by full
  semantic version rather than only the trailing Dev counter.
- The known `cloth_next.ppf.health` import is verified in the source tree,
  installed package, and extracted release ZIP.

## Beta scope

The current scope remains one cloth, one static collider, constant topology,
and the verified PPF 0.11 protocol. It is a prerelease intended for Blender
5.1.2 testing and is not a Stable release.

## External solver

The PPF Contact Solver is external software developed by ST Tech / ZOZO. It is
not bundled, mirrored, or redistributed with Cloth NeXt. A compatible separately
installed solver is required.
