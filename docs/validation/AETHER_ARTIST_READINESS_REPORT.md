# AETHER Engine: artist readiness and performance (Milestone 5.1)

Date: 2026-11-01. Same machine and inputs as the NEON acceptance (RTX 4070 SUPER, Windows 11, Blender 5.2.3 LTS).
Private test build only: no release, tag, version change or deployment.

| component | NEON acceptance baseline | this milestone |
|---|---|---|
| AETHER Engine | `492db12` | `c5eb00c` (build `df9b0459575d0a8a`, API 1.2) |
| Cloth NeXt | `a3de0a6` | `b6a0281` |

## Summary

| stage | result |
|---|---|
| A Live responsiveness | NEON Normal **19.0 → 24.9** visible updates per second (engine 30 frames/s), target ≥ 24 reached. |
| A+ Grab Cloth (Tim's request) | drag the Live cloth with the mouse, Tim's hand icon as the cursor; tested in the visible UI. |
| B Animated collider CCD | ball scene **22.5 → 9.1 ms** per frame (Normal) at 0 intersections; two real holes found and closed (fast colliders, thin folds); 4 new regression scenes. |
| C Real animated character | **not artist-ready**: the cloth gradually ends up inside the moving body (up to ~2 200 edges by frame 150), 420-590 ms per frame. A separate milestone is needed. |
| D Neck overlap | not an engine fault: the collar is modelled inside the head mesh's neck. Trimming the head collider does not remove it; the scene needs a head collider that starts above the collar. |
| E Attachment startup | analysed: the startup peak is a settling transient of the whole garment, only partly at attachments; an attachment ramp would not remove it. A settle pre-roll is proposed, not implemented. |

NEON regression: unchanged (0 cloth-cloth intersections, the same 2-6 neck edges, bit-identical resume).
Validation: engine 33/33, host tests OK, Cloth NeXt suite 2 750 passed + the adjusted icon test, GAIA real bake PASS,
GUI smoke 18/18.

**Recommendation for the first external round:** draping, fitting, static or slowly moving colliders, props and
simple animation (Live and Bake). **Not yet:** walking or fast character animation with tight garments.

## A. Live Simulation responsiveness

### Method

`neon_live_profile.py` (test environment) runs Live on NEON in the visible UI for 10 s through the panel operator
and records per timer callback: the Live tick, depsgraph evaluation (`depsgraph_update_pre` to the first post
handler), each post handler, the 3D view draw (PRE_VIEW → POST_PIXEL), draws of other editors, and the cycle gap.
Before and after were measured back to back with the same harness.

### Where the time went (Normal, before)

| per cycle (52 ms p50) | ms |
|---|---|
| Live tick (shared memory read 0.1, transform + vertex write 2.0) | 2.2 |
| depsgraph evaluation of the three preview meshes (23 356 v) | 5.4 |
| Cloth NeXt depsgraph handlers | 0.8 |
| Properties editor redrawn on **every** frame (Live tagged it) | ~6 |
| idle between the Live timer and the redraw it tagged (event loop sleep) | ~5 |
| 3D view draw (NEON: MetaHuman, rigs, hair, overlays) | 20.5-27 |

### Changes (Cloth NeXt `6a271b2`)

* only the 3D **region** is tagged on a new frame; the panels at 4 Hz; nothing when no frame arrived;
* no shared-memory read when no frame was written; single-precision transform into preallocated buffers (skipped
  for identity transforms);
* `vertices.co` writes (the attribute API sends a geometry notifier that redraws every Properties editor);
* preview meshes without custom split normals, sharp edges/faces and tool attributes;
* a window event timer while Live runs keeps the event loop from sleeping between timer and redraw.

### Result

| NEON | before | after |
|---|---|---|
| visible updates per second, Normal | 19.0 / 19.2 | **24.8 / 24.9** |
| cycle p50 / p95 | 52 / 57 ms | 39 / 47 ms |
| Fitting (engine limited: 24.1 simulated frames/s) | - | 24.0 |
| earlier acceptance harness (extra 20 ms timer) | 12.8 | 24.5 |

Pause, Reset, Stop, scene restoration and process cleanup unchanged (`neon_live.py` PASS). The remaining ~21 ms per
cycle are Blender drawing the NEON scene; with viewport overlays off NEON reaches ~26-28 updates/s. All `bpy`
access stays on the main thread. `CLOTH_NEXT_AETHER_LIVE_PROFILE=1` records per-tick read/write times.

### A+. Grab Cloth

Live panel and floating toolbar button, Ctrl Shift G in the 3D View. Left-click on a Live preview grabs the face
under the mouse with compliant runtime pins (strength 1 = the Soft Pin scale), the targets follow the mouse in the
view plane at the grabbed depth, release removes the pins. Pinned or attached vertices are never grabbed. The system
cursor is hidden and Tim's hand icon is drawn at the mouse (white with a dark halo; the fingertips are the grab
point). Tested in the visible UI with Blender's own event simulation (`grab_test.py`): the point follows a 180 px
drag, falls back after release, Stop restores the scene.

## B. Animated mesh collider CCD

### New regression scenes

`aether_collider_bench` and the engine test `moving_colliders_hard` (Cloth NeXt default cloth: 1 kg/m², 1 681 v):

| scene | 427ff4c (first CCD fix) | c5eb00c |
|---|---|---|
| ball, 1.4 cm/frame (GUI-smoke scene) | 0 / 0, 14.3 ms | 0 / 0, **9.5 ms** |
| fast ball, 2 m/s with reversals | 0 / **144 cloth-cloth**, 22.2 ms | 0 / 0, 27.5 ms (substep factor 3) |
| ball reversing at 3x speed | 0 / 0, 14.5 ms | 0 / 0, 13.6 ms |
| two balls, opposite directions | - | 0 / 0, 9.8 ms |
| thin 4 mm fold, ball sweeping under it | 0 / **4 cloth-cloth**, 14.8 ms | 0 / 0, 14.5 ms |

(cloth-collider / cloth-cloth intersections, maximum over 71 frames; mean wall time per frame.)

### Where the 3x cost came from, and the changes

* **Quiet CCD rounds were not quiet** (pre-existing since M3.5): the stale flag of round - 2 woke every second
  round, so each cycle ran the full CCD over all contacts ~60 more times. A quiet round now clears its flag.
* **Stalled reverts:** a revert round that moves no vertex now ends the cycle (the state cannot change any more);
  the remaining impact is counted as unconverged instead of being iterated to the 128-round limit.
* **Fast colliders:** a frame whose mesh colliders move more than 0.5 x the median cloth edge per substep runs with
  proportionally more substeps (max 8x, one captured frame graph per factor).
* **Carry re-check:** a CCD carry moves a vertex after the detection that bounded its motion; up to two more CCD
  cycles run, gated on the device by "the previous cycle carried a vertex".

Through Cloth NeXt (ball scene, 72 frames): Normal 22.5 → **9.1 ms**, Fitting 38.0 → **17.2 ms** per frame, 0
intersections in every frame (before the first CCD fix: 7.7 / 12.2 ms with up to 274 edges through the ball).

**Caveat (measured):** restricting the substep factor to collider parts near the cloth made the fast-ball scene leave
2 collider edges inside (a chaotic trajectory change). That variant was not kept; correctness at speed is not yet
robust against such perturbations.

## C. Real animated character

`anim_prepare.py` builds `SW04_ANIM_AETHER.blend` from the NEON working copy (never the original): the body rig is
keyframed for 150 frames at 30 fps (0.5 s rest, arm raise, walking in place with thigh/calf/pelvis motion, torso
twist with fast reversals, arm swing); the head rig follows the body rig's head bone; the body's static Mesh Cache is
disabled. Multiple garment layers, Material Zones, Object Attachments to the moving body and self-collision are all
active. Every output frame is counted.

| | Normal | Fitting |
|---|---|---|
| per frame mean / p95 | 444 / 706 ms | 466 / 810 ms |
| cloth-collider intersections, maximum | 2 274 | 2 210 |
| cloth-collider, frame 21 / 61 / 101 / 141 | - | 10 / 335 / 1 111 / 1 981 |
| cloth-cloth intersections (pants) | up to 115 | up to 128 |
| edges > 10 % strain at the end | 6-17 % | 4-18 % |
| checkpoint resume (killed at frame 75) | - | **bit-identical** |
| PC2 playback | exact | exact |

* The garments follow the motion visually (stills `results\anim\fitting_strip.png`), but cloth that gets behind the
  moving body surface never comes back out; the count grows from frame ~20 on.
* The collider itself does not self-intersect from the motion (constant ~650 head/body overlap edges at the neck).
* More substeps help stability (with fewer substeps the pants exploded: strain 43x) but do not make it tight.
* The cost is dominated by the substep factor (hands move fast) and CCD on tight, pinched cloth.

**Conclusion:** tight garments on a deforming, moving character need a dedicated milestone: automatic recovery of
cloth behind collider surfaces (the engine's E4 untangling, not yet used by Cloth NeXt), signed collider contacts
for edge-edge pairs, and a cheaper treatment of skinned colliders. NEON (static) does not validate character motion.

## D. Neck overlap

> **Corrected in Milestone 5.2** ([AETHER_ANIMATED_CHARACTER_REPORT.md](AETHER_ANIMATED_CHARACTER_REPORT.md)): the neck
> edges were the same engine mechanism as section C (cloth edges across smooth collider edges without a signed
> contact). With signed collider edge contacts the neck shows 0 edges; the conclusion below is superseded.

The MetaHuman head and body meshes interpenetrate at the neck (553 head vertices behind the body surface).
`neck_prepare.py` builds a NEON copy whose head Collider is a copy of the head without those faces (the original head
still renders). Results (Fitting):

| head collider | cloth-collider edges |
|---|---|
| original head | 2-6 |
| trimmed: faces behind the body removed (434 faces) | 6 |
| trimmed: faces within 12 mm outside the body removed too (662 faces) | 6 |

The remaining edges belong to the collar of Garments.001 at z ≈ 1.30 m and cross the **lower neck of the head mesh**:
the collar is modelled inside the head's neck surface. A merged collider would not help (the surfaces still cross),
and weakening collision detection is not acceptable. **Scene preparation:** use a head collider that starts above
the collar (or a head collision proxy without the neck), or move the collar outside the head's neck. No engine
change.

## E. Attachment startup stability

`startup_strain.py` splits the NEON Fitting startup strain into edges within 2 cm of an attachment point (12 % of the
edges), the Inlay zone and all other edges:

| frame | attachment max / p99.9 | Inlay max / p99.9 | other max / p99.9 |
|---|---|---|---|
| 3 | 15 / 10 % | 19 / 11 % | 39 / 4 % |
| 7 | 37 / 20 % | 13 / 11 % | 37 / 11 % |
| 9 | 41 / 20 % | 31 / 26 % | 27 / 10 % |
| 15 | 17 / 11 % | 22 / 17 % | 23 / 6 % |

The peak is a **settling transient of the whole garment**: the fitted shape is not an equilibrium for these (heavier)
materials, and the first frames overshoot; attachment edges carry it most strongly, but other edges peak just as
high. A stiffness ramp on attachments would let the garments sag further before the attachments take hold, which is
extra cloth motion, not less. Not implemented. Proposal: an optional **settle pre-roll** (frames simulated before the
bake range, not written to the cache, recorded in the sidecar), similar to Marvelous' settling step.

## Benchmark separation (NEON Normal, c5eb00c)

| | value |
|---|---|
| solver compute (engine, 248 frames) | 6.25 s = 25.2 ms per frame |
| host communication + PC2 writing (engine startup 0.3 s, publish 0.4 s) | ~0.7 s |
| Blender validation / input capture | 1.2 s / 4.0 s |
| end-to-end bake | 14.95 s |
| Live: tick / depsgraph / 3D view draw per update | 2.2 / 5.4 / ~21 ms |

Marvelous: the earlier comparison stays exploratory (different fabrics); no new claims.

## Validation

| check | result |
|---|---|
| engine tests | 33/33 (new: `moving_colliders_hard`) |
| host tests | OK |
| Cloth NeXt test suite | 2 750 passed, 35 skipped; the icon test failed on the new cursor and now accepts its dark halo (9/9) |
| real-Blender e2e (static/animated collider, zones) | PASS |
| GUI smoke (visible UI) | 18/18 |
| Live on NEON (pause/reset/stop/restore/cleanup) | PASS |
| Grab Cloth (visible UI, event simulation) | PASS |
| GAIA real bake (12 frames) | PASS, playback exact, no solver processes left |
| NEON regression: cloth-cloth / neck / resume | 0 / 2-6 / bit-identical |
| no orphaned engine processes | PASS |

## Remaining limitations

1. Animated characters with tight garments (section C). This is the blocker for character work.
2. Correctness of the fast-collider path is sensitive to small trajectory changes (section B caveat).
3. Neck/collar geometry needs scene preparation (section D).
4. Startup settling transient (section E); no settle pre-roll yet.
5. Live preview rate is bounded by Blender's own scene drawing (~21 ms on NEON).
6. No strain limiting (Stretch Limit not enforced, reported).

## Recommendations for the first external artist round

* Scope: draping and fitting on static or slowly animated colliders, props, flags and sheets, Live with Grab Cloth.
* Ask testers to report: scene size, preset, per-frame times (shown by the Bake window), any cloth inside colliders.
* Ship with the test environment's launcher and `verify_engine.py`; no account needed.
* Exclude walking or fast character animation until the character-collision milestone lands.
