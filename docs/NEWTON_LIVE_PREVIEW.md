# Newton Live Preview (experimental)

Cloth NeXt can run an optional Newton Physics worker for non-destructive cloth
preview and an explicitly selected experimental offline Bake. Newton is not
bundled with the extension and is never installed into Blender's Python. PPF
remains the default production Bake backend and its recovery data is independent
of all Newton state.

## Verified environment

- Newton Physics `1.4.0` (official tag `v1.4.0`)
- Warp `1.15.0`
- external CPython 3.11 virtual environment
- Windows 11, NVIDIA GeForce RTX 4070 SUPER, driver 610.74

Newton requires Python 3.10 or newer and its only required package dependency
at this version is `warp-lang>=1.15.0`; Cloth NeXt pins the verified pair
exactly. The installed package metadata identifies both Newton 1.4.0 and Warp
1.15.0 as Apache-2.0, which permits commercial use and redistribution subject
to the license's notice and attribution conditions. Warp ships additional
third-party notices (including CUDA runtime components) that must also be
reviewed before redistribution. Cloth NeXt redistributes neither package and
therefore leaves their complete installed notices in the external environment.

The verified Windows environment occupied 542.02 MiB before user-generated
Warp JIT cache data. Its installed package set was Newton 1.4.0, Warp 1.15.0,
NumPy 2.4.6, pip and setuptools; no provider SDK is added to Blender. Download
size is deliberately not promised because wheel compression and package-index
metadata may change while the exact installed versions remain pinned.

The managed environment is stored outside the repository, extension and PPF
installation:

```text
%LOCALAPPDATA%/ClothNeXt/newton/
  current.json
  versions/1.4.0-warp-1.15.0/venv/
  sessions/<session-id>/results/
```

Use **Preferences > Add-ons > Cloth NeXt > Newton · Principia > Install
Newton**. The action requires explicit confirmation and online access. It
creates a dedicated Python 3.11 environment, installs exact versions, probes
imports and CUDA, then atomically publishes `current.json`. It never mutates
Blender's interpreter. Developers may point at an existing verified
environment with `CLOTHNEXT_NEWTON_PYTHON`.

## Runtime boundary

Blender captures immutable triangles, pins, material parameters and identities
on its main thread. A persistent owned subprocess receives bounded newline JSON
commands over stdin and returns bounded protocol JSON over stdout. Diagnostics
remain on separately captured stderr. Result positions are complete atomic
`.npy` artifacts with session, scene, frame, vertex-count and SHA-256 checks.
The Blender timer accepts a result only after all fields, path ownership,
checksum, array shape and finite values have been verified.

The protocol version is 1 and supports `health`, `capabilities`,
`create_preview`, `update_target_frame`, `pause`, `reset`,
`restore_snapshot`, `update_parameters`, `status`, `cancel`,
`destroy_preview`, and `shutdown`. Hot parameter updates currently fail
closed and require a rebuild.

Only the newest requested timeline target is retained. Physics still advances
sequentially. Backward requests restore the nearest bounded snapshot at or
before the target, or the always-retained initial state, then simulate forward.
The worker is not restarted for each frame.

The preview uses a temporary Cloth-NeXt-owned mesh in world coordinates. The
source object is hidden only for the active session; its mesh is never edited.
The preview object is excluded from rendering and is removed on disable,
failure, reload or unregister. The worker is assigned to an owned Windows Job
Object and is shut down cooperatively before bounded terminate/kill fallback.

## Solver decision

Both `SolverVBD` and `SolverStyle3D` are real protocol choices; one is never
silently substituted for the other. On the same four-vertex pinned smoke mesh
on the verified workstation, VBD model build was about 0.27 s and the full
start/forward/rewind run about 8.9 s. Style3D model build was about 4.29 s and
the run about 14.0 s. VBD is therefore the recommended interactive default.
Style3D remains an internal experimental comparison path; its projected
panel-rest adapter currently accepts planar cloth only. These timings are not
a claim of equal physical output.

## Current feature boundary

Supported: multiple triangle Cloth objects with a common Bake range, gravity,
Blender FPS and range, time scale, static and animated/deforming triangle
colliders with stable topology, static hard pins, experimental material
mapping, Follow Animation pins with substep-interpolated targets, optional VBD
self-contact, pause, forward play/scrub, bounded rewind,
worker crash recovery, and non-destructive per-Cloth viewport display.

Rejected before worker startup: mismatched Cloth Bake ranges,
topology-changing animated colliders or animated Pin meshes, pressure, sewing,
Edit Mode, topology-changing Cloth modifiers, unsupported object roles and
negative-scale transforms. Newton
recovery checkpoints and preview persistence across Blender restarts are not
implemented. Preview parameters are explicitly experimental and do not claim
PPF parity.

Geometry, object UUID/role, transforms, topology, pins, frame timing, forces,
material and preview quality are part of the preview identity. Tracked geometry
or transform updates mark the session stale; unrelated selection and viewport
display changes do not. Changing only the timeline frame never rebuilds the
model.

## Experimental offline Bake

The Simulation panel offers **Solver: Production (Lunelle) / Preview
(Principia)**. Production is the
default and is unchanged. Selecting Newton routes the normal `clothnext.bake`
operator through the same immutable snapshot and owned worker boundary used by
preview. Every complete, checksummed worker result is converted from Newton
world coordinates back to the source object's local coordinates and streamed
into the existing atomic `StreamingPc2Writer`. Only after the full PC2 and its
SHA-256-authenticated sidecar are published does the existing Cloth NeXt Mesh
Cache attachment path switch playback to the new file.

The sidecar records Newton backend, solver, protocol, Newton and Warp versions;
its fingerprints and cache names are distinct from PPF. Cancellation or worker
failure removes only the private partial PC2 and leaves the previous playback
untouched. Newton Bake does not provide Recovery checkpoints and the UI states
that limitation next to the experimental backend. Live Preview may still be
used while final Bake remains set to PPF; a Newton Bake itself requires preview
to be disabled so two sessions cannot control the same Cloth.

## Validation

Pure tests cover contracts, coordinates, protocol bounds, state transitions,
snapshot retention, installer publication, process ownership and duplicate
handler prevention. Real tests use the pinned environment for hanging cloth,
static collider and self-contact cases. `tools/blender_newton_preview_gate.py`
drives actual Blender play, pause, forward/backward scrub, initial restore,
disable and worker-crash restart and writes machine-readable evidence. A real
test without the configured environment reports **Newton unverified**, never a
success.
