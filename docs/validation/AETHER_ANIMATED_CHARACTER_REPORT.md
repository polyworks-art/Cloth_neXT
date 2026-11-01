# AETHER Engine: robust animated character simulation (Milestone 5.2)

Date: 2026-11-01. Same machine and inputs as Milestone 5.1 (RTX 4070 SUPER, Windows 11, Blender 5.2.3 LTS).
Private test build only: no release, tag, version change or deployment. GAIA unchanged.

| component | baseline (5.1) | this milestone |
|---|---|---|
| AETHER Engine | `c5eb00c` (build `df9b0459575d0a8a`) | `ff006e8` (build `88d640279111ed8a`, API 1.2 unchanged) |
| Cloth NeXt | `e8e04e4` | `9643674` + this report |

## Summary

The animated NEON character (150 frames: arm raise, walking in place, torso twist with fast reversals, arm swing;
three garment layers, Material Zones, Object Attachments to the moving body, self-collision) now bakes **without
any cloth-body or cloth-cloth intersection in any frame**, in all three presets, about twice as fast as before.

| animated NEON, baked in Blender | 5.1 (`c5eb00c`) | 5.2 |
|---|---|---|
| cloth-collider intersecting edges, maximum over 150 frames | Normal 2 274 / Fitting 2 210 | **0 / 0 / 0** (Normal / Animation / Fitting) |
| cloth-cloth intersections, maximum | up to 115 / 128 | **0 / 0 / 0** |
| per frame, mean | 444 / 466 ms | **224 / 286 / 318 ms** |
| checkpoint resume (killed at frame 75, Fitting) | bit-identical | **bit-identical** (all 3 PC2) |

Two root causes were found and fixed; neither fix raises iterations, collision thickness or substeps:

1. **Cloth edges crossing smooth collider edges were not resolved and then held in the crossed state** (engine).
   Fixed with *signed collider edge contacts*.
2. **Cloth-to-collider attachment offsets were kept in world space** (Cloth NeXt), so on a rotating limb the
   attachment target pointed into the body and pulled the cloth through it. Fixed: the offset now lives in the
   bound triangle's local frame.

The engine's E4 untangling is now connected to Cloth NeXt as automatic recovery, behind an experimental switch
(off by default). The static NEON neck overlap reported in 5.1 as a scene problem was the same engine mechanism as
cause 1 and is gone (2-6 edges → 0). **Correction to the 5.1 report, section D:** its conclusion ("not an engine
fault, needs a different head collider") was wrong.

## 1. Root cause analysis

### Instrumentation

`AETHER_DIAG_PENETRATION=<file>` (engine, `95df363`; graph replay is switched off while it runs) checks the exact
cloth-collider and cloth-cloth edge-triangle intersections after **every substep** and writes one JSON line per
substep (counts, contacts, CCD rounds used, unconverged CCD cycles, carry re-checks) plus one line for every
intersecting pair that is **new** against the previous substep: cloth edge, triangle, garment, signed distance of
both edge vertices at substep start and end (mm), relative normal velocity, collider step (mm), and whether the last
contact detection held a contact for that pair (VF, EE, both or none).

Per-frame tools in the private test environment: `replay.py` (replays a dumped Cloth NeXt BakeRequest through the
real BakeRunner without Blender and counts intersections in every output frame with `aether_mesh_quality`),
`count_intersections.py` (the same for bakes from Blender), stills via `neon_render.py`.

### Findings (animated NEON, Normal, `c5eb00c`)

* The first new pairs appear at frame ~20 (arm raise). **None of them comes from a fast collider:** the relative
  normal velocity of the crossing edges is ~0 and the collider step is well below the cloth thickness; CCD reported
  no unconverged cycles. The cloth did not tunnel.
* Every new pair is a **cloth edge whose two vertices are outside the body** while the edge itself cuts across a
  collider edge (a rounded shoulder, elbow, knee or neck rim). The intersection test counts the edge as crossing a
  body triangle, but no vertex-face contact can see it (no vertex is inside).
* The engine created edge-edge *resolution* contacts only for **sharp** collider edges (dihedral > 30°). On a smooth
  skinned body almost no edge qualifies, so these crossings had no contact at all.
* Where an edge-edge contact existed it was **unsigned** (its normal followed the current closest-point direction),
  and the collider-aware CCD treats an unsigned EE pair symmetrically: once the edges had crossed, CCD held them in
  the crossed state instead of letting them leave. The count could therefore only grow, which is the "progressive
  disappearance" into the body.
* After the engine fix 4 edges remained in frames 147-149 (Normal) and around frame 94 (Animation). Diagnostics put
  all of them at vertex 21913, which is the **Leg attachment of Garments.002**: the attachment target itself was
  inside the leg. Cause 2 above.

### Small reproduction scenes

`aether_collider_bench` (engine repo) got five new scenes beside the 5.1 ones: `elbow`, `elbow_tight` (sleeve on a
bending, skinned elbow), `twist`, `twist_tight` (tube on a twisting torso) and `ridge_start` (cloth that starts with
1 140 edges across a smooth ridge, the "invalid initial state" class). `deforming_colliders` was added to the
engine test suite (now 34 tests).

| scene | before (unsigned, sharp edges only) | after |
|---|---|---|
| elbow / elbow_tight | 0 / **72** (66 still at the end), 36.7 ms per frame | 0 / **0**, 12.2 ms |
| twist / twist_tight | 0 / 0 | 0 / 0 |
| ridge_start (starts crossed) | 1 140 → 1 140, stuck | 1 140 → 528 (then stalls) |
| ball, fast_ball, reverse_ball, two_balls, folded | 0 everywhere | 0 everywhere, CCD reverts and impacts 2-4x lower |

## 2. Animated collider contact reliability

**Signed collider edge contacts** (`kContactEEC`, engine `95df363`): a cloth edge against a collider edge now gets
a contact whose normal is oriented by the collider's outward direction at that edge (the mean of its two face
normals at detection time), not by the current closest-point direction. A cloth edge behind the collider is pushed
back out instead of being held at the gap, and CCD lets an EEC pair that starts behind the surface move out
(it still blocks motion inwards). All collider edges get these resolution contacts
(`collider_edge_contacts_all`), not only sharp ones. Endpoint cases stay with vertex-face contacts.

Unchanged: vertex-face contacts, cloth self-collision (cloth-cloth EE stays unsigned), static colliders, pins,
sewing, CCD truncate/slide/revert rounds and the carry logic from 5.1. No contact is dropped silently: the per-frame
statistics still report unconverged CCD cycles and the stall exit, and the diagnostics list every unresolved pair.

Both switches are scene parameters (`collider_edge_signed`, `collider_edge_contacts_all`, default on) and can be
turned off for comparison with `AETHER_EXP_COLLIDER_EDGE_SIGNED=0` / `AETHER_EXP_COLLIDER_EDGE_ALL=0`.

**Attachments in the surface frame** (Cloth NeXt `9643674`): the Cloth→Collider attachment offset is stored in the
local frame of the bound collider triangle (edge, normal, binormal at rest) and transformed with that triangle every
frame. A sleeve attached to a rotating forearm now keeps its offset outside the arm.

## 3. Automatic penetration recovery (experimental)

The engine's E4 untangling is reachable from the host (`"untangle": {"frames": N}` in a frame input; the frame event
carries an untangle report). Cloth NeXt requests it every frame when `CLOTH_NEXT_AETHER_EXPERIMENTAL_RECOVERY=1`
is set; the switch is part of the bake's scene digest, so a recovery bake never resumes from a normal checkpoint
and vice versa. **It is off by default and not offered in the UI.**

| class | handled by |
|---|---|
| 1 invalid initial state (cloth starts inside) | E4 for vertices inside; edges across with all vertices outside: not recoverable (see ridge_start) |
| 2 newly created intersection | prevented by signed EEC contacts; E4 resolved the one 4-edge event of the pre-attachment-fix build within 1 frame |
| 3 cloth behind a collider | EEC contacts push it back; E4 on top |
| 4 cloth trapped between two surfaces | not observed on NEON; E4 islands where they exist |
| 5 geometrically impossible (discretization) | reported, not fixed: an edge longer than the feature it crosses has no inside vertex for E4 |
| 6 conflicts with pins / attachments / sewing | E4 respects pinned vertices; the attachment cause was fixed at the source |

Measurements: animated NEON Normal with recovery 215 ms per frame, 0 / 0 intersections (same as without, since
nothing is left to recover); on the pre-fix build it found and resolved the 4-edge event at +7 % cost. On
`ridge_start` E4 did not improve the result (528 → 674 at the end): that scene is class 5. Corrections are bounded
by the E4 step limit; no explosive correction was seen in any run.

## 4. Performance

| animated NEON (replay, engine only, 149 frames) | per frame mean / p95 | collider / cloth-cloth max |
|---|---|---|
| `c5eb00c` Normal | 429 / 700 ms | 2 274 / 115 |
| signed EE, sharp edges only | 398 / 730 ms | 424 / 25 |
| signed EE, all edges | 204 / 305 ms | 4 / 0 |
| + attachment fix: Normal / Animation / Fitting | **214 / 280 / 285 ms** | **0 / 0** |

The speed-up is a consequence of correctness: cloth that stays outside does not keep the CCD in revert rounds.

Split for the Blender bake (Normal, 149 frames, 40.5 s end-to-end): engine 33.4 s (224 ms per frame), Blender-side
collider capture and validation ~6 s, host communication and PC2 writing ~1.7 s (replay: 33.5 s end-to-end vs
31.8 s engine). Static NEON is unchanged at 26 / 39 / 44 ms per frame; the animated scene is still ~7x more
expensive than static because the hands move far enough to need a collider substep factor of up to 8.

Rejected experiments (kept for reference, not merged):

* **Substep factor from collider motion near the cloth only** (instead of the whole collider): tried twice; it broke
  `fast_ball` once and later made the animated scene slower and worse (6-8 intersections, 233-313 ms).
* **Coarser collider substeps** (`collider_substep_edge_fraction` 2.0): explodes (3 530 / 40 636 intersections).
  1.0 is correct on NEON (0 / 0, 189 ms) but has too little margin; 0.5 stays the default.
  (`AETHER_EXP_SUBSTEP_EDGE_FRACTION` remains as an experiment switch.)

## 5. Real character acceptance (animated NEON)

`SW04_ANIM_AETHER.blend` is a working copy built from the NEON working copy; `F:\16_Cloth_neXt\SW04\SW04_NEON.blend`
was never opened for writing. Baked through Cloth NeXt in Blender (`m52_validate.ps1`), every output frame checked:

| preset | per frame mean / p95 | collider / cloth-cloth, every frame | strain p99 max (3 garments) |
|---|---|---|---|
| Normal | 224 / 363 ms | 0 / 0 | 17 / 24 / 21 % |
| Animation | 286 / 494 ms | 0 / 0 | 14 / 25 / 22 % |
| Fitting | 318 / 530 ms | 0 / 0 | 16 / 20 / 22 % |
| Normal + experimental recovery | 215 / 340 ms | 0 / 0 | 21 / 36 / 22 % |

The motion covers the focused cases of the brief in one clip: walking (trousers on thighs, knees), arm raise
(sleeve/shoulder), torso twist with fast direction changes, elbows, body and head as two colliders, Cloth→Collider
attachments on the moving body, and recovery after an interrupted bake (killed at frame 75, resumed bit-identical).
Stills: `results\m52v\anim_fitting_front_strip.png` (frames 20, 45, 75, 110, 150).

Garments.002 (trousers) keeps ~15 % of its edges above 10 % strain throughout the motion (5.1: 6-17 %); this is the
stiff waistband and cargo straps being stretched by the walking pose, not a contact effect.

## 6. Regression and determinism

| check | result |
|---|---|
| engine tests (`aether_tests`) | 34 / 34 |
| host tests | OK |
| collider CCD regression scenes (ball, fast_ball, reverse_ball, two_balls, folded, elbow, twist) | 0 intersections |
| static NEON Normal / Animation / Fitting | 0 collider (5.1: 2-6 at the neck) / 0 cloth-cloth; 26 / 39 / 44 ms |
| static NEON resume (killed at 120) | bit-identical |
| ball scene Normal / Fitting | 0 / 0, 9.8 / 19.7 ms |
| Cloth NeXt suite | 2 751 passed, 35 skipped (TETHYS builds not present) |
| Blender e2e (static and animated collider, multi material, animated pins, sewing, Material Zones, attachments, Live, cancel, kill+resume, device lost, incompatible checkpoint, lifecycle, offset range, GAIA routing, performance) | all PASS (lifecycle failed once only while the suite ran in parallel and counted its engine process; PASS alone) |
| GUI smoke in the visible UI | 18 / 18 |
| Grab Cloth (Blender event simulation) | PASS |
| real GAIA bake (unchanged GAIA backend) | PASS |

**Checkpoint compatibility:** the engine build id changed (`df9b0459575d0a8a` → `88d640279111ed8a`). Checkpoints
from the 5.1 build are rejected through the build identity and the bake starts fresh; this is covered by the e2e
`incompatible` scenario. The recovery switch is part of the scene digest, so recovery and normal bakes never share
checkpoints.

## Diagnostics available to support

* `AETHER_DIAG_PENETRATION=<file>`: per-substep penetration log as described in section 1 (slow; for analysis).
* Per-frame step statistics: CCD rounds, unconverged cycles, stalls, carry re-checks, collider substep factor.
* With experimental recovery: the untangle report per frame (detected, resolved, remaining) in the bake progress.

## Remaining limitations

* **Edges across a feature with all vertices outside** (cloth coarser than the collider detail, `ridge_start`): the
  signed contacts reduce but do not remove a crossed *starting* state, and E4 cannot help. Scenes must start
  without such crossings (Fitting a garment to a body before animating does this).
* Animated characters are ~7x slower per frame than static scenes (collider substeps for fast hands).
* The substep fraction 0.5 has a margin of about 2x (1.0 is still correct on NEON, 2.0 is not).
* Recovery is experimental: not in the UI, not validated on scenes that actually need it beyond the one event above.
* One animation clip and one character; faster motion (running, jumps) and other bodies are not validated.

## Readiness recommendation

Animated characters with tight, layered garments are **ready for the private artist round**, with the scene starting
in a non-intersecting state (fit first, then animate) and walking-speed motion like the test clip. Keep the
experimental recovery off. Not yet claimed: running or acrobatic motion, very coarse cloth on detailed bodies, or
production use.
