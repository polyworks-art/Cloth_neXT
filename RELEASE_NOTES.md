# Cloth NeXt 0.3.0-dev.8 — experimental Dev test build

## Blender 5.1 playback ownership fix

Playback ownership is now stored authoritatively on the cloth object because
Blender 5.1 does not support ID properties on Mesh Cache modifiers. Failed
modifier creation is rolled back instead of leaving a duplicate behind, and
orphaned Cloth NeXt modifiers from earlier Dev builds are recovered only when
their type, canonical name, controlled cache filename, and cache directory all
match. A successful attach reuses one recovered modifier and removes extras.

## Blender result-pump recovery

The Blender result timer now has a top-level exception boundary and a separate
active-run watchdog. If Blender removes the timer after a Python exception,
the failure is reported with its traceback instead of leaving the companion,
HUD, and Physics panel indefinitely on `IMPORTING`. If the timer disappears
without a terminal error while a run is still active, the watchdog restores it
so the completed worker result can be consumed and attached.

## Non-blocking Blender status delivery

Bake status updates to the companion are now queued and coalesced on the IPC
thread instead of writing to the socket from Blender's main thread. A stalled
or disconnected companion can therefore no longer leave Blender blocked on
“Importing PC2 cache” before the playback modifier is created. Only the newest
pending status is retained, keeping progress delivery bounded during fast
state transitions.

## Streaming cache update

The next Dev snapshot replaces the memory-heavy playback-cache post-process
with incremental PC2 streaming. Large bakes no longer retain or duplicate the
complete animation in Python memory; cache files and sidecars publish
atomically, cancellation preserves the previous valid result, and the UI
reports real cache-frame and finalization progress. No solver or quality
parameter changed.

Owned local solver runs now read completed result frames directly from Cloth
NeXt's controlled project directory instead of downloading every frame again
through the local TCP server. External servers retain the validated TCP path.
The importer validates path containment and final frame sizes, closes each
memory mapping explicitly, reuses extraction buffers, and writes PC2 frame
payloads without an additional Python `bytes` copy. Cache finalization timings
now separate flush, filesystem sync, replacement, and validation.

Playback attachment now reuses the existing Cloth NeXt Mesh Cache modifier
instead of removing and reconstructing it. All inactive settings are applied
before the new cache filepath is switched in one final operation. This avoids
repeated Blender dependency-graph rebuilds that could leave complex scenes
apparently blocked on “Importing PC2 cache” long after the PC2 file was ready.
Attach, filepath-switch, ownership, and old-cache cleanup timings are recorded
separately for real-scene diagnosis.

## Solver Quality presets

Scene-wide Solver Quality now offers Low, Medium, High, and Extreme presets
with clearer artist-facing labels. The four documented numeric PPF settings
remain authoritative, and manually adjusted combinations are shown as Custom.
High preserves Cloth NeXt's established defaults; Extreme is explicitly marked
as potentially expensive.

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
