# AETHER Engine integration (Milestone 5, experimental)

**Status:** experimental development integration for testing builds. It is not a production release.

**Base:**

* Cloth NeXt Dev 2.9.52 (`bc329a5`).
* AETHER Engine `d74cb33`: `AETHER Engine.exe` 0.4.6-dev, protocol 1, API 1.1.

**What an artist can do:**

* select **AETHER** in Preferences › Engine;
* simulate Cloth interactively;
* bake to the usual Mesh Cache playback;
* resume a bake that was interrupted by a crash or a lost GPU.

GAIA users are not affected: a GAIA selection keeps baking through the unchanged solver path.

Test report: [validation/AETHER_ENGINE_INTEGRATION_TEST_REPORT.md](validation/AETHER_ENGINE_INTEGRATION_TEST_REPORT.md).

## 1. Architecture and modified files

```text
Blender main thread                                   worker thread              own process
validate_scene (shared) -> AetherPreparation  ---->  BakeRunner  ---- stdin/stdout ---->  AETHER Engine.exe
  (rest state, animated inputs per frame)              (client.py)  <--- shared memory ---  (CUDA)
attach (shared transactional Mesh Cache import) <----  StreamingPc2Writer + sidecar
```

**New files:**

| file | content |
|---|---|
| `cloth_next/aether_engine/client.py` | the AETHER reference client, **vendored unchanged** from the AETHER repository (`client/aether_engine_client.py` at `d74cb33`); the only protocol implementation |
| `cloth_next/aether_engine/engine.py` | engine discovery, manifest integrity (size, SHA-256, protocol, API, build id), `--version` probe, AETHER status → CNX code mapping |
| `cloth_next/aether_engine/materials.py` | material mapping (section 4) |
| `cloth_next/aether_engine/bake.py` | pure bake runner on a worker thread: engine process, input sequencing, PC2 streaming, sidecars, checkpoints, recovery record, resume verification |
| `cloth_next/blender/aether_engine_backend.py` | capability check, main-thread scene capture, timer-pumped bake job (Bake window, controller), playback attach, Resume / Discard operators |
| `cloth_next/blender/aether_engine_live.py` | Live Simulation (preview objects, main-thread timer, play/pause/reset/stop) |
| `tests/test_aether_engine.py` | pure tests plus engine round trips (bake, kill + resume, device loss, cancel, incompatible recovery) |
| `tools/blender_aether_engine_e2e.py`, `tools/run_aether_blender_e2e.py` | real-Blender acceptance (14 scenarios, isolated user resources) |
| `tools/aether_material_calibration.py` | measured reference cases for the material mapping |
| `tools/stage_aether_engine.py` | stages `AETHER Engine.exe` plus its manifest (not used by any workflow yet) |

**Modified files:**

| file | change |
|---|---|
| `simulation/backends.py` | `BackendId.AETHER_ENGINE` with its capability declaration and material mapping kinds |
| `blender/solver_backends.py` | routing: Engine = GAIA → the unchanged PPF path; AETHER → the native engine. Also backend status, settings fingerprint and bake dispatch |
| `blender/preferences.py` | the Engine selector: choosing AETHER sets the hidden `aether_engine_chosen` flag and **never writes the solver registry**. Existing GAIA selections stay GAIA |
| `updater/engines.py` | AETHER description now names the native engine. The upstream PPF build no longer appears as "AETHER" in testing builds, per Tim's decision |
| `blender/object_properties.py` | scene settings `aether_bake_quality` (default Fitting), `aether_live_quality` (default Normal), `aether_checkpoint_interval` (24) |
| `blender/physics_ui.py` | readiness of the selected backend; AETHER section (presets, Live Simulation, not-enforced notes, Resume / Discard); backend-aware cache-stale check |
| `blender/registration.py` | registers the new operators, the load handler and shutdown |
| `blender/solver_test.py` | Bake-window Cancel and the "another engine is running" lock also cover AETHER (two call sites) |
| `tools/validate_extension.py` | the source tree must not contain a staged AETHER Engine (the same rule as TETHYS) |

## 2. Backend registration and capabilities

| capability | AETHER Engine |
|---|---|
| cloth (mesh), multiple Cloth objects | yes |
| per-cloth materials | yes |
| self-collision (Enable Contact) | yes |
| static and animated **mesh** colliders, including collision proxies | yes, CCD-checked |
| pins: static hard, static soft, Follow Animation hard and soft | yes. Hard: prescribed (registered pins or runtime hard pins). Soft: compliant attachments |
| sewing (Sewing definitions between Cloth objects or within one) | yes; seams close over 1 s |
| Normal / Animation / Fitting presets | yes; separate defaults for Live (Normal) and Bake (Fitting) |
| live preview, recovery | yes |
| Rods, Soft Bodies, Rigid Bodies, mixed simulation | **refused** |
| Forces (wind, air), Water Flow / FLIP, pressure | **refused** (GAIA keeps the FLIP workflow) |
| Material Zones, Friction Regions, Object Attachments, Pin Target Objects, Advanced Pin Motion, Soft Constraints | **refused** |
| animated, parented or constrained Cloth transform | **refused**: animate the cloth through pins instead |
| modifiers that change the vertex count | **refused** (CNX-E103) |
| Cloud bakes | **refused**: AETHER bakes run on this computer |

* A refusal is cheap and visible: the Bake button shows the reason, and the bake fails before any process starts.
* Nothing unsupported is silently ignored.
* There are no moving **analytic** colliders: Cloth NeXt colliders are meshes, so the AETHER host's release gate for
  fast analytic colliders is never reached from Blender.
* E4 untangling is not reachable from Cloth NeXt.

## 3. Coordinates, frames and process

**Coordinates:** AETHER takes Blender world space (Z up) directly. There is no PPF Y-up conversion.

* Rest state: the evaluated mesh at Bake Start (Live: at the current frame), with Cloth NeXt playback muted.
* World = `matrix_world @ local`.
* PC2 = `inverse(matrix_world at Bake Start) @ world`, so it plays back object-local.

**Frames:** PC2 sample *n* is Blender frame `Bake Start + n` and engine frame index *n*.

* Sample 0 is the rest state.
* The inputs of engine step *n−1* are the poses at frame `Bake Start + n`.
* Animated colliders and Follow-Animation pin targets are sampled once per frame on the main thread before the
  engine starts. They become the bake's **saved input sequence**, digested for recovery.
* The Mesh Cache modifier gets `frame_start = Bake Start`.
* Tested: Bake Start 11 gives 30 samples, sample 0 equals the rest mesh exactly, and playback matches the PC2 exactly.

**Threads:**

* every `bpy` access (validation, `frame_set`, depsgraph, mesh writes, modifiers) happens on the main thread, from a
  `bpy.app.timers` pump with a 40 ms capture budget per tick;
* the worker thread sees only immutable numpy snapshots (`BakeRequest`) and writes files.

**Process lifecycle:**

* one owned `AETHER Engine.exe` per bake or per Live Simulation;
* startup negotiates the protocol (1) and API (1.1) and reports engine build and GPU;
* the process is owned three ways: a kill-on-close job object, an owner-PID watchdog in the engine, and the closing
  stdin pipe;
* a GPU-less system fails with `AE_ERROR_NO_DEVICE` → CNX-E133. There is no CPU fallback;
* a missing or damaged engine fails with CNX-E131, a version mismatch with CNX-E132;
* a lost device (engine exit 3) gives CNX-E164: "The GPU was lost (driver reset or device fault). The bake can be
  resumed from its last checkpoint";
* tested: 5 Live cycles plus 3 bakes leave no `AETHER Engine.exe` running.

No PolyWorks account is involved.

## 4. Material mapping (`aether_engine/materials.py`, mapping version 2)

Cloth NeXt material values are PPF wire conventions (`docs/PPF_PARAMETER_MAPPING.md`). They are **converted**, not
copied:

| Cloth NeXt | AETHER | kind |
|---|---|---|
| Surface Weight (kg/m²) | density | exact |
| Stretch Resistance (density-normalized) | membrane modulus = Stretch × Surface Weight (N/m) | approximate (StVK membrane) |
| Sideways Response | Poisson ratio (< 0.5) | exact |
| Bend Resistance | B = Bend × Weight / 14.3491 × 2.86967·10⁻³ N·m (inverts the MIT calibration in `MATERIAL_LIBRARY.md`), handed to AETHER as 0.6 B | approximate, calibrated |
| Shape / Fold Damping (s) | Rayleigh membrane / bending damping (s) | approximate |
| Surface Grip | Coulomb coefficient = grip × 0.5 (the coefficient the default solver receives) | approximate; colliders use the cloth's value |
| Collision Gap | cloth half-gap = gap / 2 | approximate; colliders use the cloth's gap |
| Stretch Limit | **not enforced**, reported (below) | unsupported |
| Surface Offset, Shrink, Inflate / Pressure, Permanent Stretch or Bends, flat bend rest, Shape-Preserving model, loose-edge Sewing | **refused** | unsupported |
| Sewing Strength 0–100 | seam stiffness = strength / 100 × 10 × mean membrane modulus (2μ+λ) of both panels | approximate |
| Soft Pin pull strength | attachment stiffness = pull × 10 × (2μ+λ) | approximate |

**Measured reference cases** (`tools/aether_material_calibration.py` through the real engine, Fitting, settled;
`docs/validation/aether_material_calibration.json`):

| case | simulated / analytic |
|---|---|
| hanging strip, Stretch 250 / 1000 / 4000: elongation g L² / (2 Stretch) | 0.981 / 0.995 / 0.999 |
| clamped cantilever, Bend 1 / 4 / 10: tip deflection ρ g L⁴ / (8 B) | 0.932 / 1.054 / 1.085 |

* **Membrane:** the density un-normalization is confirmed directly.
* **Bending:** AETHER's discrete dihedral bending was measured 1.5–1.9× stiffer than the continuum B it is given
  (ratios 0.53–0.65 over five resolutions and aspect ratios). The mapping therefore passes 0.6 B, leaving a residual of
  ±10 % that depends on the mesh.
* The MIT presets' physical bending equals their measured value in SI (test).

**Stretch Limit:**

* 74 of 75 bundled presets enable it, so refusing it would refuse nearly every garment.
* AETHER bakes without strain limiting and says so:
  * as a panel note ("… · not enforced by AETHER");
  * in the bake result message;
  * as `details.aether.not_enforced` in every cache sidecar.

## 5. UI behavior

**Preferences › Engine:** AETHER (native engine) or GAIA.

**Physics › Cloth NeXt › Simulation**, with AETHER selected:

* engine readiness, e.g. "AETHER Engine 0.4.6-dev · build …";
* **Bake** and **Live** preset selectors;
* notes for not-enforced controls;
* **Start Live Simulation**, then Pause/Resume, Reset and Stop;
* "An AETHER bake was interrupted" with **Resume Bake** / **Discard**;
* the shared Bake button, cache folder button, Bake window, progress and Cancel.

The PPF quality buttons are hidden while AETHER is selected; they would not apply.

**Live Simulation:**

* The artist's meshes are never written. Each Cloth gets a temporary preview object (marked `cloth_next_aether_live`,
  same materials), and the original is hidden.
* **Stop:**
  * removes the previews;
  * restores visibility, the timeline frame and the muted playback modifiers;
  * tested: vertex coordinates, object set and frame are identical afterwards.
* Frames come from the seqlocked shared-memory double buffer. Outdated frames are skipped; a torn frame is impossible
  (checksum).
* Animated colliders and Follow-Animation pins are sampled when the timeline frame changes and sent as one batched
  input.
* Changing the preset needs a new Live session. A preset change is never presented as preserving state.
* File load stops the Live Simulation. Leftover previews from a crash are removed on the next start.

## 6. Bake and recovery

**Reused:**

* the shared validation;
* the Bake controller, Bake window, progress and Cancel;
* `StreamingPc2Writer` (one PC2 per Cloth: `<cache>/cn_aether_<object-uuid>.pc2`);
* `cache_metadata` sidecars (same schema; `identities.solver` names AETHER, engine build, GPU and preset);
* the transactional multi-object playback attach `_attach_playback`, with rollback on failure and
  `baked_solver_backend = AETHER_ENGINE`.

There is no second cache system.

**Settings fingerprint:**

* `sha256(AETHER_ENGINE, preset, mapping version, shared settings fingerprint)`;
* a preset or mapping change makes the cache stale;
* GAIA fingerprints are unchanged.

**Recovery files (AETHER-only, never mixed with GAIA recovery):**

* `<cache>/cn_aether_recovery_<uuid>/aether_<frame>.aeckpt` plus a manifest, written by the engine transactionally
  every `aether_checkpoint_interval` frames (24), with the previous checkpoint kept;
* `cn_aether_recovery_<uuid>.json`, the Cloth NeXt recovery record:
  * engine build and GPU;
  * preset, fps and range;
  * scene digest (geometry, materials, pins, seams, collider rest);
  * **input digest** (the saved animated input sequence);
  * the partial PC2 files.

**Resume (Resume Bake):**

1. Recapture the scene and compare the record. A changed preset, fps, range, scene, input sequence or object set is
   refused with CNX-E129 and the exact reason.
2. The engine rejects another build, another GPU or another scene itself (manifest plus checkpoint fingerprints).
3. Drop checkpoints newer than the frames already in the partial PC2. The engine runs ahead of the cache by up to the
   ring size; without this a resume would leave a gap. This was found by the device-loss test.
4. Resume from the newest remaining checkpoint, or from Bake Start if none remains.
5. Recompute the frames between the checkpoint and the cache end and **verify them bit for bit** against the cached
   bytes; then append. A mismatch stops the bake (CNX-E129). A frame is never duplicated, omitted or overwritten.

A cancelled or failed bake leaves no playback change and a resumable partial. Starting a normal Bake (start fresh)
discards it.

## 7. Error mapping (existing codes only)

| situation | AETHER | Cloth NeXt |
|---|---|---|
| engine missing / damaged / foreign file | – | CNX-E131 |
| protocol, API or build mismatch | AE_ERROR_API_VERSION | CNX-E132 |
| no CUDA GPU (unsupported GPU) | AE_ERROR_NO_DEVICE | CNX-E133 |
| process could not start | – | CNX-E134 |
| device lost / process ended | AE_ERROR_DEVICE_LOST, exit 3 / other exits | CNX-E164 |
| numerical failure | AE_ERROR_NUMERICAL | CNX-E165 |
| out of GPU memory | AE_ERROR_OUT_OF_MEMORY | CNX-E166 |
| invalid scene value | AE_ERROR_INVALID_ARGUMENT | CNX-E104 |
| invalid topology / non-finite geometry | – | CNX-E103 / CNX-E109 |
| unsupported feature | – | CNX-E106 (scene reason in the message) |
| checkpoint incompatible / corrupt, resume refused | AE_ERROR_CHECKPOINT_* | CNX-E129 |
| cache folder / PC2 write failure | – | CNX-E121 / CNX-E181 |
| protocol error from the engine | exit 2 | CNX-E145 |
| internal | AE_ERROR_INTERNAL | CNX-E199 |

Errors without a specific code use the shared classifier.

## 8. Performance (real Blender 5.2.3 LTS, RTX 4070 SUPER)

Draped sheet on a sphere, 48 frames, end to end (preparation, engine, PC2, sidecars, attach):

| vertices | preset | bake frames/s | engine ms/frame | engine startup | main-thread tick p95 / max | GPU memory used (incl. desktop) |
|---|---|---|---|---|---|---|
| 10.2k | Normal | 37.2 | 14.4 | 0.25 s | 0.1 / 37 ms | 2386 → 2744 MB |
| 10.2k | Fitting | 30.4 | 21.3 | 0.19 s | 0.1 / 45 ms | → 2697 MB |
| 50.6k | Normal | 7.95 | 100.5 | 0.34 s | 0.1 / 233 ms | → 3480 MB |
| 50.6k | Fitting | 8.62 | 90.2 | 0.33 s | 0.1 / 255 ms | → 3278 MB |

| Live (Normal) | startup | displayed frames/s | timer tick p95 / max |
|---|---|---|---|
| 10.2k | 0.29 s | 24.2 (paced to the scene fps) | 1.1 / 1.2 ms |
| 50.6k | 0.67 s | 6.8 (engine-bound) | 4.0 / 4.7 ms |

* **UI responsiveness:** the main thread is busy 0.1 ms per tick while the engine runs. The maximum is the one-time
  publication at the end: PC2 SHA-256 sidecar, depsgraph update and modifier swap. That is the same shared step as
  every Cloth NeXt bake (233–255 ms at 50k).
* **Small scenes:** about 0.2 s of engine startup per bake; a 30-frame 1k bake finishes in under 1 s.

## 9. Distribution (prepared, not done)

* **Convention (as for TETHYS):**
  * the binary goes to `vendor/aether/windows-x64/AETHER Engine.exe`;
  * the release workflows run `python tools/stage_aether_engine.py "<vendor>/AETHER Engine.exe"`;
  * that writes `cloth_next/bin/AETHER Engine.exe` plus `aether_engine_manifest.json`: size, SHA-256, protocol, API
    version, engine version and **engine build id**.
* **Runtime integrity:**
  * `engine.locate` authenticates size and hash;
  * the `--version` probe must report the manifest's build id;
  * protocol and API must match, otherwise CNX-E131/E132.
* **Source tree:** `tools/validate_extension.py` refuses a staged engine in the source tree. The packaged-ZIP check
  for AETHER is still to be added, like `_validate_tethys`.
* **Development:** `CLOTH_NEXT_AETHER_ENGINE` points a development build at an engine outside the extension, like
  `CLOTH_NEXT_TETHYS_ENGINE`.
* **Not done in this milestone:** no workflow changes, no vendored binary, no release, no version change.

## 10. Known limitations

* Experimental; validated on one GPU (RTX 4070 SUPER), Windows only (the engine is Windows-only).
* Stretch Limit is not enforced (reported). Bending carries a ±10 % mesh-dependent residual after calibration. Friction
  and collision gap of colliders follow the cloth's values.
* Seams close over 1 s from their separation at Bake Start. A seam already closed in the rest pose is simply held.
  Seam strength scales a modulus-based default and is not the GAIA force unit.
* Soft Pins map pull strength to a modulus-scaled stiffness. Per-vertex pin weights are not used.
* Cloth objects must not have animated, parented or constrained transforms.
* Live Simulation previews do not run the original's modifier stack on top of the simulated mesh. They show the
  simulated cage with the original materials.
* The end-of-bake publication blocks the UI once for about 0.25 s at 50k vertices (shared publication step).
* Bit-identical resume requires the same engine build and GPU; anything else is refused, never approximated.

## 11. Remaining release blockers

1. Vendor and stage the engine in the release workflows, add the packaged-ZIP integrity check, and sign the binary.
2. Strain limiting in AETHER, or an artist-facing decision on the not-enforced Stretch Limit.
3. Validation on more GPUs, drivers and real garments. The **NEON / SW04** acceptance test is next, including the 1:1
   Marvelous comparison without FLIP.
4. Material Zones, Friction Regions, Object Attachments and Advanced Pin Motion, or permanent refusals documented in
   the UI.
5. Collider-aware CCD for analytic colliders, if analytic colliders ever reach the AETHER path.
6. A per-collider friction and gap model in AETHER.
7. A GUI acceptance pass with the Bake window open (the automated runs disable the window; the controller path is the
   same).
