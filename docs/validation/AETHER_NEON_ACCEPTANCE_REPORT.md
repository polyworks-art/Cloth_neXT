# AETHER Engine: private test build and NEON acceptance

Date: 2026-11-01. Hardware: NVIDIA GeForce RTX 4070 SUPER (driver 13040), Windows 11, Blender 5.2.3 LTS.
Marvelous Designer 2026.0.315 (GPU mode). Everything here is a **private test build**: no Cloth NeXt version
change, no tag, release, deployment or channel publication, no change to the installed Cloth NeXt, GAIA, Blender
preferences or caches.

| component | revision |
|---|---|
| AETHER Engine | `polyworks-art/AETHER@492db12` (branch `claude/project-thread-4jr3rm`), `AETHER Engine.exe` 0.4.6-dev, build id `cc262830aca537d3`, protocol 1, API 1.2 |
| Cloth NeXt | `polyworks-art/cloth_next@d8d1ae0` (branch `claude/project-thread-4jr3rm`) |
| baseline of the brief | AETHER `d74cb33`, Cloth NeXt `86cfc4a` (changes since then: section 6) |

## Summary

* **Private build:** installed in an isolated folder, engine identity verified (hash, build id, commit, protocol,
  API, startup). GUI smoke test in the visible UI: **18/18 PASS**. Small real GAIA bake: **PASS**.
* **NEON (SW04):** the full scene except FLIP runs through the real AETHER workflow in all three presets, with
  Material Zones and Object Attachments. **0 cloth-cloth intersections** in every preset and frame. Cloth-body: 0 to 6
  edges, all at the neck where the MetaHuman head and body meshes overlap. Checkpoint resume after a hard engine kill
  is **bit-identical**. Live Simulation works on NEON (real time at 30 fps in Normal).
* **Acceptance testing found and fixed four real defects** (section 6). The most serious one: cloth went through
  **animated** mesh colliders (Tim's ball scene, up to 274 edges per frame). It is fixed with a collider-aware CCD.
* **Marvelous:** same meshes (47 017 triangles), same avatar (0 mm), same fps and frame count. The **fabrics differ**:
  stiffness cannot be read from Marvelous, and the weights in the .blend are 1.5 to 6 times higher. This is therefore
  **not a 1:1 comparison** of physics. Per-frame compute time is close: Normal equal, Animation and Fitting
  AETHER 1.2 to 1.3 times faster. **This is no evidence that AETHER is generally faster.**
* **Artist readiness:** ready for **testing builds** for artists. Not ready as marketing evidence for speed claims.
  Open points are in section 7.

## 1. Phase A: private test build

Location: `C:\Users\timch\ClothNeXt-AETHER-Test` (its `README.md` has the install, test and removal procedure).

* `engine\AETHER Engine.exe` + `engine-identity.json` (SHA-256 `07ea0a80…66b4f5`, build id, commit). The baseline
  engine `d74cb33` is kept as `AETHER Engine.d74cb33.exe`.
* `source\`: detached checkout of Cloth NeXt; `blender-user\`: isolated `BLENDER_USER_RESOURCES` (own preferences,
  own extensions, the add-on linked from `source\cloth_next`).
* `Start Blender (AETHER Test).cmd` sets `BLENDER_USER_RESOURCES`, the supported override `CLOTH_NEXT_AETHER_ENGINE`
  and the developer Bake window. Production packaging and `docs/RELEASE_POLICY.md` are unchanged.
* `verify_engine.py`: all checks true for the current build (`sha256`, `build_id`, `commit`, `protocol` 1, `api` 1.2,
  clean exit); device RTX 4070 SUPER.
* No account is needed for AETHER. Choosing AETHER never writes the solver registry.

## 2. Phase B: GUI smoke test (visible UI, regular panels)

`tools/blender_aether_gui_smoke.py`, run in the test environment with the current build. Screenshots are of Blender
areas and of the "Cloth NeXt Bake" window only. Results: `results\gui_smoke2\gui_smoke.json`.

| check | result |
|---|---|
| Engine selection in Preferences, readiness (build shown), Bake button enabled | PASS |
| Live: start (0.18 s), preview moves, Pause, Reset, Stop restores the scene | PASS |
| Bake Normal and Fitting with Bake window and progress, PC2 playback exact (error 0) | PASS (72 frames in 2.5 s / 2.2 s) |
| Cancel offers Resume; Resume bit-identical to an uninterrupted Fitting bake | PASS (resumed at frame 25, 8 frames verified) |
| Unsupported feature message ("not supported by the AETHER Engine: Shrink") | PASS |
| no orphaned `AETHER Engine.exe` | PASS |

**GAIA regression (real bake, not routing only):** a 12-frame GAIA 0.26.0-dev (`cc9b0c13ec2f`) drape with a collider,
through `tools/blender_engine_bake_e2e.py`, finished in 105 s with exact playback and no solver processes left. The
GAIA solver copy lives outside `%LOCALAPPDATA%` (known `.cash` FileExistsError for app-spawned headless bakes). The
later commits touch only AETHER modules; the GAIA path is unchanged.

## 3. Phase C: SW04 / NEON preflight

Original `F:\16_Cloth_neXt\SW04\SW04_NEON.blend` was only read (hash unchanged). Working copy:
`neon\SW04_NEON_AETHER.blend` (`neon_prepare.py`): FLIP Domain removed, the Water Flow / hydrodynamic / wet-fabric
flags of the cloth objects off, cache folder moved into the test environment.

| item | finding | AETHER |
|---|---|---|
| Cloth | 3 garments: 14 946 + 4 379 + 4 031 = 23 356 vertices, 47 017 triangles | supported |
| Colliders | `SW_04_body_lod0_mesh` (30 455 v) and `SW_04_head_lod0_mesh` (24 049 v) | supported (static here, see below) |
| Animation | **none**: the file has no actions; the rigs are driven by MetaHuman drivers and do not move in any of the 249 frames (also with Tim's add-ons). Confirmed by Tim. Without FLIP, NEON is a **drape/settle test** | - |
| Frame range | 1-249 at 30 fps | supported |
| Material Zone "Inlay" on Garments.001 (594 faces: Stretch 10, Bend 2, Friction 0.1) | was refused | **now supported** (section 6) |
| Object Attachments | 3 Cloth to Collider (40/31/31 points), 1 Cloth to Cloth (84 points) | supported |
| Stretch Limit | Garments.001 4 %, Garments.002 32 % | baked, **not enforced** (no strain limiting in AETHER; reported in panel, notes and sidecar) |
| Mesh | Garments and Garments.001 have 410 / 239 edges shared by three or more triangles (panel junctions) | was refused, **now supported** (section 6) |
| Subdivision modifiers | off in the viewport | - |
| MetaHuman DNA driver add-on | missing in the isolated environment (`character_dna_native_solve_v1` errors) | affects facial deformation only; the head does not move anyway |
| FLIP | removed in the copy | not part of the test |

No simplification remains except the removed FLIP.

## 4. Phase D: NEON production test

`neon_run.py` drives Cloth NeXt's real `AetherBakeJob` (validation, capture, engine, PC2, sidecars, transactional
playback attach) in background Blender. Intersections are counted exactly (`aether_mesh_quality`, the edge-triangle
counter of every AETHER milestone) on every second frame (125 frames); strain is per edge against frame 1.

### Performance (249 frames, 248 simulated)

| preset | end to end | of which validation / capture / engine | per frame mean / p50 / p95 / max | GPU memory |
|---|---|---|---|---|
| Normal | 14.0 s | 1.3 / 4.0 / 5.8 s | 23.2 / 23.0 / 25.7 / 36.3 ms | +0.6 GB |
| Animation | 17.4 s | 1.4 / 4.4 / 8.5 s | 34.4 / 34.0 / 37.6 / 54.9 ms | +0.9 GB |
| Fitting | 17.6 s | 1.2 / 4.2 / 9.4 s | 37.9 / 37.7 / 40.1 / 50.7 ms | +0.7 GB |

Compute per simulated second (30 frames): Normal 0.70 s, Animation 1.03 s, Fitting 1.14 s.
Engine startup 0.3 s. Main thread during a bake: tick p50 0.03 ms, p95 48-52 ms, max 0.8-0.9 s (the capture steps
and the final playback attach).

### Quality

| preset | cloth-cloth | cloth-body | strain p99 (worst frame, per garment) | edges > 10 % |
|---|---|---|---|---|
| Normal | 0 | 2-4 edges in 112 of 125 frames | 4.4 / 11.1 / 10.9 % | 0.1-1.3 % |
| Animation | 0 | 2-4 edges | 2.9 / 6.2 / 7.1 % | 0.1-0.6 % |
| Fitting | 0 | 2-6 edges | 2.5 / 7.0 / 5.8 % | 0.1-0.5 % |

* **Worst frames:** the body intersections are one or two cloth edges of Garments.001 at the neck
  (`(-0.05, 0.03, 1.31)`), crossing both the head and the body mesh where those two meshes interpenetrate. The cloth
  cannot lie outside both surfaces there. They appear around frame 11-27 and stay.
* **Startup:** single edges stretch strongly in the first ~10 frames (up to 35-89 % max) while the garments settle;
  hot spots are at the attachment lines (hip, chest). The Inlay zone (Stretch 10) is very soft by design: in Normal
  its edges reach 150 % once.
* **Stability:** no NaN, no explosion, no drift; the garments stay in place over 249 frames. Stills:
  `results\neon\fitting\stills` (frame 1, frame 249, front and back).
* **PC2 playback:** exact (max error 0) for all garments and presets.

### Checkpoint recovery

Fitting, `AETHER Engine.exe` killed at frame 120: Cloth NeXt reports "The bake can be resumed from its last
checkpoint". Resume continues at frame 120. All three PC2 files are **bit-identical** (SHA-256) to the uninterrupted
Fitting bake.

### Live Simulation on NEON (visible UI, panel operators)

| | Normal | Fitting |
|---|---|---|
| startup | 1.73 s | 1.74 s |
| simulated frames per second | 30.0 (real time at 30 fps) | 23.6 |
| preview frames shown per second | 12.8 | 14.5 |
| Blender main-thread tick (requested 20 ms) | p50 76 ms, p95 84 ms, max 438 ms | p50 66 ms, p95 81 ms, max 128 ms |
| Pause / Reset / Stop, scene restored, no orphaned process | PASS | PASS |

The UI runs at about 13-15 updates per second while Live runs: copying 23k vertices into the preview meshes takes
most of each tick. This is the main responsiveness item.

## 5. Phase E: Marvelous comparison

Marvelous ran inside Tim's own MD session through the job runner (`marvelous\md_agent.py`), documented Python API
only, black-box (no reverse engineering). The project was reloaded before each run and at the end; it was never
saved.

### Equivalence

| | Blender (AETHER) | Marvelous | equal? |
|---|---|---|---|
| garment mesh | 23 356 v, 47 017 tris | 23 354 v, 47 017 tris (welded export), particle distance 10 mm | **yes** |
| start positions | | median 1.7 mm apart (p99 6.5 mm) | nearly |
| avatar | 54 504 v | 54 504 v, 0.000 mm apart | **yes** |
| avatar animation | none | none (Tim) | **yes** |
| frames / fps | 248 steps at 1/30 s | 248 × `Simulate(1)` at 1/30 s | **yes** |
| gravity, units | -9.81 m/s², m | MD default, mm | yes |
| collision thickness | 1 mm gap | 1 mm additional thickness per pattern | yes |
| fabric weight | 1000 / 465 / 560 g/m² (scene) | swimwear 160, leather 300, baselayer 150 g/m² | **no** (matched in a second AETHER run) |
| fabric stiffness, bending, shear | Cloth NeXt values | not readable through the API | **no** |
| hardware | RTX 4070 SUPER, same machine, runs one after another | same | yes |
| timing scope | engine per-frame time (Progress.step_ms) | `Simulate(1)` wall time, exports excluded | comparable |

**Because the fabrics differ, this is not a 1:1 physics comparison.** The timing comparison is valid for the same
mesh, contacts and frame count, but not for the same material behaviour.

### Results (248 frames)

| | per frame mean / p95 | sim time total | cloth-cloth | cloth-avatar | strain p99 at the end |
|---|---|---|---|---|---|
| Marvelous Normal | 23.8 / 27.8 ms | 5.9 s | 0 | 18 edges from frame 0 | 9.5 % |
| Marvelous Animation | 45.1 / 49.6 ms | 11.2 s | 0 | 18 | 9.6 % |
| Marvelous Fitting | 45.0 / 50.6 ms | 11.2 s | 0 (2 at frame 50) | 18 | 2.6 % |
| AETHER Normal, MD weights | 24.7 / 27.3 ms | 6.1 s | 0 | 0 | up to 12 % (soft Inlay) |
| AETHER Animation, MD weights | 34.5 / 36.5 ms | 8.6 s | 0 | 2-4 | up to 6.7 % |
| AETHER Fitting, MD weights | 38.5 / 40.1 ms | 9.6 s | 0 | **0** | up to 5.4 % |

* Marvelous' first frame takes 0.6-0.7 s (warm-up); it is included in its mean.
* Marvelous' 18 avatar intersections are already in the project's start state and never resolve.
* "AETHER end to end" (14-18 s) also includes validation, input capture and engine startup, which the Marvelous
  numbers do not contain. Compared on equal terms (simulation only): Normal equal, Animation 1.3x, Fitting 1.2x in
  AETHER's favour **for this scene**.
* Earlier, unmatched Marvelous benchmarks are not used as evidence here.

## 6. Defects found and fixed during acceptance

| defect | effect | fix |
|---|---|---|
| a Cancel latched by an earlier bake was never cleared | the next AETHER bake was cancelled during validation | Cloth NeXt `c6d7c0e` |
| edges shared by 3+ triangles (Marvelous panel junctions) | NEON refused ("non-manifold edge") | AETHER `345055e`: one bending hinge per pair of neighbouring panels at their rest angle; test `panel_junction` |
| CCD held mesh colliders at their substep end | cloth went into **animated** colliders (ball scene: up to 274 edges, 21 cm deep) | AETHER `427ff4c`: collider-aware CCD, cloth carried with the collider; test `moving_mesh_collider`; ball scene 0 intersections. Cost: about 3x per frame in that scene |
| Material Zones refused | NEON needed a reduced scenario | AETHER `492db12` (API 1.2, per-triangle materials) + Cloth NeXt `d8d1ae0`; test `material_zones`, e2e `material_zones` |

AETHER tests 32/32, host tests OK; Cloth NeXt `tests/test_aether_engine.py` 15/15 with the engine, real-Blender e2e
scenarios PASS.

Lesson: the earlier e2e and GUI tests never counted cloth-collider intersections. Every acceptance bake now goes
through `aether_mesh_quality`.

## 7. Open points before artist-facing or marketing use

1. **Live responsiveness:** 13-15 UI updates per second on NEON; move the preview vertex upload off the critical path.
2. **CCD cost with animated colliders:** pinched folds now exhaust CCD rounds (reported as unconverged), about 3x per
   frame in the ball scene.
3. **Neck overlap:** cloth between overlapping collider meshes (MetaHuman head/body) keeps 2-6 edges inside; a
   per-collider resolution or a merged collider would remove it.
4. **Startup shock:** single edges stretch up to ~90 % in the first frames at attachment lines; a ramp for
   attachments (like seam closing) should be checked.
5. **No strain limiting:** Stretch Limit is not enforced (reported).
6. **A real 1:1 Marvelous comparison** needs the Marvelous fabric physics (stretch, shear, bending) transferred into
   the Blender scene, or the Blender materials recreated in Marvelous. Until then: no speed claims beyond "comparable
   per-frame time on the same mesh".
7. Validation on more GPUs, drivers and garments; a scene with real avatar motion (NEON has none).
