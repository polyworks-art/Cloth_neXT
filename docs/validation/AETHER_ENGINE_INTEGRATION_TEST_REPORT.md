# AETHER Engine integration: test report (Milestone 5)

**Environment:**

* Windows 11, RTX 4070 SUPER;
* **Blender 5.2.3 LTS** (Steam). 5.2.1 is no longer installed; 5.2.3 is the current LTS patch;
* `AETHER Engine.exe` 0.4.6-dev, build `e8d5b4860ec5d1fb` (AETHER `d74cb33`);
* Cloth NeXt branch `claude/project-thread-4jr3rm` based on Dev 2.9.52 `bc329a5`.

**Isolation:**

* Real Blender ran with isolated user resources. A temporary `user_default` extension repository linked to the
  checkout (`tools/run_aether_blender_e2e.py`).
* The installed extension, Tim's preferences and the solver registry were not modified. Choosing AETHER never writes
  the registry.

## 1. Real Blender acceptance (`tools/blender_aether_engine_e2e.py`): 14/14 PASS

Raw report: `aether_engine_e2e/report.json`.

| # | scenario (brief item) | result | evidence |
|---|---|---|---|
| 1 | static sheet onto a mesh collider | PASS | 30 PC2 samples; playback vs PC2 max error 0.0; fell 0.20 m; cache backend AETHER_ENGINE, not stale |
| 2 | animated character-style mesh collider (keyframed through the drape) | PASS | 36 samples, playback error 0.0 |
| 3 | several Cloth objects with separate materials (Silk, Denim) | PASS | both caches complete; different mapped modulus and bending in the sidecars |
| 4 | animated pin targets (Follow Animation via shape-key animation) | PASS | pinned vertices travel 0.400 m as animated |
| 5 | sewing two independent panels (8 cm apart) | PASS | max seam gap after closure 0.95 mm |
| 6 | Live Simulation: start, pause, reset, stop | PASS | startup 0.20 s; 73 frames shown in 3 s (24.3 fps, paced); timer tick max 0.4 ms; pause holds; reset returns to frame 0; after stop: mesh coordinates, objects, visibility and frame identical, no engine process |
| 7 | bake with complete PC2 output | PASS | all bake scenarios: complete PC2 plus sidecar, `inspect_cache` READY, attached transactionally |
| 8 | cancel during a bake | PASS | CANCELLED; no playback change; Resume offered; no engine process left |
| 9 | terminate `AETHER Engine.exe`, resume from a checkpoint | PASS | CNX-E164; Resume from frame 24, 7 cached frames verified; final PC2 **bit-identical** to the uninterrupted bake |
| 10 | simulated device loss and recovery | PASS | injected fault after frame 29 → CNX-E164 "The GPU was lost …"; the checkpoint at 24 was newer than the 21 cached frames, so it was dropped and the bake resumed from Bake Start; 21 frames verified; PC2 **bit-identical** |
| 11 | incompatible checkpoint rejection | PASS | preset changed → CNX-E129 "… the quality preset changed (presets are never changed silently)" |
| 12 | repeated startup and shutdown | PASS | 5 Live cycles (0.44–0.50 s each) plus 3 bakes, 0 `AETHER Engine.exe` afterwards |
| 13 | nonzero Bake Start and PC2 playback | PASS | Bake Start 11: 30 samples; modifier `frame_start` 11; sample 0 equals the rest mesh exactly; playback error 0.0 |
| 14 | existing GAIA baking unchanged | PASS (routing) | the GAIA selection routes to the unchanged PPF path; AETHER routes to the native engine; GAIA code paths are not modified (diff) |
| – | performance (10k / 50k, Normal / Fitting, bake plus Live) | PASS | `docs/AETHER_ENGINE_INTEGRATION.md`, section 8 |

**Scenario 14** is a routing check only. A full GAIA bake was not rerun in this milestone:

* the GAIA bake code is unchanged in the diff;
* the shared files touched (`solver_test.py`: Cancel routing and the busy lock; `physics_ui.py`: backend-aware
  status) were checked against the suite below.

## 2. Pure and engine round-trip tests (`tests/test_aether_engine.py`): 15/15

* Material mapping:
  * membrane and bending derived, not copied;
  * MIT presets map to their measured bending;
  * unsupported controls refused;
  * Stretch Limit reported as not enforced.
* Engine discovery: manifest authentication, tamper detection, API mismatch.
* Every AETHER status maps to a registered CNX code.
* Against the real engine:
  * complete bake: PC2 plus sidecars READY, sample 0 = rest;
  * killed engine → resume, bit-identical;
  * device loss → resume;
  * cancel → resumable partial, nothing published;
  * incompatible recovery: preset, scene or range change refused.

## 3. Existing Cloth NeXt suite

`python -m pytest tests -q --ignore=tests/test_built_artifacts.py` with `CLOTH_NEXT_AETHER_ENGINE` set:
**2751 passed, 35 skipped, 0 failed**.

* The PPF and GAIA real-solver integration tests and the TETHYS engine tests are skipped without their executables,
  as before.
* A first run found two regressions, both fixed:
  * a helper name collided with an existing draw-forbidden function name;
  * a missing `scene` attribute on a test context.

## 4. Material calibration (`aether_material_calibration.json`)

| case | simulated / analytic |
|---|---|
| hanging strip, Stretch 250 / 1000 / 4000 | 0.981 / 0.995 / 0.999 |
| cantilever, Bend 1 / 4 / 10, after the 0.6 discrete-bending calibration | 0.932 / 1.054 / 1.085 |

## 5. Not covered

* the Bake window GUI. The automated runs disable the window; the controller and companion path is shared with every
  other bake;
* GPUs other than the RTX 4070 SUPER;
* production garments. NEON / SW04 is the next acceptance test.
