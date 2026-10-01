# GAIA Ripping capability audit — official 0.23

Date: 2026-10-01. **Result C — connection breaking only**, through stopped,
checkpointed runs with unchanged topology. There is no supported live SHELL
topology mutation. This is an audit and isolated experiment, not a production
Ripping implementation or release. Reactive Materials was not revisited.

## Source identity and scope

Inspected official checkout `../ppf-contact-solver`, commit
`344bcb906203db19f3ea59ccd011d5a297f90390`, release `2026-09-27-20-44`, wire
0.23 / schema 2. Inspected frontend, solver/core/formats/server crates,
constraint and energy kernels, statistics, checkpoint code, docs, protocol
history, tests, debug scenarios and examples. Real experiments use the existing
isolated official Windows CUDA bundle in TEMP; no installation was replaced.

Repository-wide searches covered tear/tearing/rip/ripping/fracture/damage/crack,
breaking/broken/failure, deletion, splitting and topology mutation. Hits for
teardown, broken software, rest-tracking boundary artifacts and mesh-cleaning
operations do not implement fracture. No solver damage evolution, crack
propagation, failure-triggered element deletion or supported runtime mesh
splitting was found. This negative search is supplemented by the positive
load/update/checkpoint paths below; absence of a keyword alone is not proof.

Source links below are pinned to the audited revision.

## Physical topology and runtime mutation

[Mesh representation](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/mesh.rs#L11)
stores triangle faces as 3-index columns, edges as 2-index columns, hinges as
4-index columns, vertex counts and neighbor lists. `Mesh::new` derives these
from densely indexed element buffers. The FFI dataset has corresponding fixed
arrays. Shared vertex indices mechanically connect adjacent triangles: reducing
a material coefficient does not give the same vertex two independent positions.

Baraff-Witkin and ARAP operate on face deformation/rest matrices; shell bending
uses hinges. Strain limiting contributes a barrier based on deformation
singular values. These are force/energy mechanisms on existing elements, not
connectivity deletion. The assembly pipeline applies elasticity, stitches,
strain limiting and contact; it has no fracture step or failure mask that
duplicates a shared vertex or disconnects adjacent faces.

| Candidate | Official support | Consequence |
| --- | --- | --- |
| Replace vertices/triangles while held | No reload path | Cannot create independent tear sides |
| Disable shell elements/edges/hinges while held | No supported failure API found | No arbitrary or progressive fabric fracture |
| Change stitches while held | No stitch reload | Python setters change authoring state, not the running solver |
| Animated stitch stiffness | Excluded from per-triangle material animation; per-body-only in frontend | No live load-driven stiffness schedule |
| Change spatial material maps while held | Not a supported live input | No live damage response through maps |
| Authored pin release | `PinHolder.unpin(time)`; solver skips expired pin blocks | Scheduled kinematic release, not load-driven seam fracture |
| Scene-wide velocity/force schedules | Supported held reload | Motion control does not disconnect fabric |
| Save, set stitch-row stiffness to zero, rebuild preserving output, resume | Public APIs; isolated CUDA experiment below | Existing stitch connections can release without changing topology |
| Rebuild a changed shell mesh and resume | Old dataset and meshset are restored | Not a supported topology/state transfer |

[Held reload](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/backend.rs#L828)
only reloads `dyn_param.txt` and force fields when `inputs_updated` is present.
It does not reload geometry, pins or stitches. Public `update_params` and
`update_force_field` are not generic scene-reload calls. The server runs a
separate frontend build worker and launches a solver process; uploading or
building authoring data does not mutate that process's in-memory scene.

## Plasticity: the seven questions

[Plastic rest state](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/plastic_state.rs#L6)
consists of inverse rest matrices and rest bend angles.
[Creep kernels](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/kernels/plasticity/plasticity.kernel.cpp#L43)
move singular values toward a yield band and creep rest angles outside a dead
zone, with timestep-aware rate `1-exp(-rate*dt)`.

1. Permanent deformation: **yes**, through evolved rest shape.
2. Reduced restoring force: **yes**, by reducing elastic deviation from rest;
   this is not an element-damage or stiffness-deletion mechanism.
3. Loss of structural connectivity: **no**.
4. Two independent sides: **no**, shared vertices remain shared.
5. Real tear: **no**, plasticity alone cannot create one.
6. Spatial mapping: **yes**, official maps can target plasticity and thresholds;
   the animated triangle whitelist also includes stretch/bend plasticity keys.
7. Newly computed held updates: **no**. Prebuilt animation evolves on its
   authored timeline, but held input reload does not refresh those maps/tables.

## Sewing and connection release

[Public `FixedScene.set_stitch`](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/frontend/_scene_.py#L1679)
accepts indices, barycentric weights and independent row stiffnesses.
[Stitch kernel](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/kernels/energy/model/stitch.kernel.cpp#L118)
multiplies gradient and Hessian by that stiffness: zero contributes no spring
force/Hessian for finite valid geometry. This is a genuine mechanical release,
not polygon hiding. The spring's length cap is not a break threshold.

[Per-step constraints](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/scene.rs#L2941)
reconstruct stitch rows from the loaded scene. Therefore a *new process* can
use new row stiffnesses after a preserved-output rebuild. Keep row count,
indices and weights unchanged:
[driver validation](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/driver/state.rs#L3137)
requires exactly the built stitch count because fixed sparsity registers the
stitch blocks. Removing rows or changing endpoint indexing is not the tested
release path.

The local CNX `attachments.py` exports attachments as `cross_stitch` rows and
Sewing uses the same primitives. Such rows are candidates for checkpointed
breakable connections. Cloth/rigid, cloth/cloth, button/plate and rope endpoint
attachments require separate endpoint/type/collision tests; they were not all
certified by a shell-only experiment. A pinned region may have a pre-authored
unpin time, but dynamic load-driven pin editing is not supported while held.

## Dynamic-state continuity and collision

[Save](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/backend.rs#L716)
retains a build-time dataset and meshset, plus per-frame dynamic state and
matching plastic rest state. Dynamic state includes current/previous positions,
time and previous timestep; retaining these preserves the solver's momentum
representation rather than estimating velocity from playback frames.
[Resume](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/main.rs#L218)
loads that dataset, the checkpoint's plastic state, and backend mesh/state,
then copies current/previous positions into the dataset. This explains why a
new stitch list of the *same structure* can be applied, and why freshly exported
triangle topology is not a replacement for checkpointed topology.

Position and momentum state survive the tested zero-stiffness rebuild path.
Plastic-state continuity is supported by the source for matching topology but
was not exercised in this non-plastic fixture. A restarted process initializes
contact/device structures; old transient contact solver state is not promised
to survive. No proof of collision correctness under a newly split topology
exists. Multiple same-structure stitch releases are feasible, with restart cost
at each failure boundary rather than a live mutation.

Contact remains active for pre-split surfaces, including while their seam is
connected. Coincident duplicates can violate initial intersection/contact-gap
requirements. Permanent start-intersection exemptions can also affect later
contact and must not be added indiscriminately to conceal this. The minimal
fixture uses separated panels with clean initial checks; it does not certify
coincident tear-line contact, post-release folding or self-collision stress.

## Fixed-topology and predefined tear lines

Exploding every triangle gives `3F` vertices for `F` triangles, versus roughly
`F/2` vertices in a large regular interior mesh: about six times the vertex
count, before adding seam springs. It removes every original inter-face bending
hinge; point springs do not reproduce continuous membrane/bending mechanics.
It adds O(F) seam constraints, with 36 registered vertex-block combinations per
six-slot stitch, extra collision candidates and duplicated attribute/UV/output
mapping. No performance or physical-equivalence evidence makes this practical.
Do not use it as arbitrary Ripping.

An authored path with duplicated boundary vertices would keep numerical
topology fixed and allow a sewn boundary to release. It loses original
cross-boundary hinges and substitutes spring mechanics; UV/attributes and
mapping need duplication at authoring time. This is a plausible future sewn
tear-line model, but **Result B is not established**: intact boundary mechanics,
load-driven damage, contact at duplicate boundaries and practical performance
have not passed a strip experiment. The verified scope is release of existing
connections, so the classification remains **C**, not full tearing or certified
Tear Lines.

## Damage signals and anisotropy

[Public frame inspection](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/frontend/_session_inspect_.py#L581)
provides current output positions. Public statistics are per-object aggregates:
location, velocity/speed, acceleration, angular quantities, surface area/area
stretch, volume/volume stretch, rod length/length stretch and contact count,
with availability masks. They do not expose per-face stress, stitch tension or
a damage field. Plastic checkpoints are recovery artifacts, not a public
per-element inspection API to mutate.

CNX can derive geometric triangle deformation or seam endpoint separation from
current world positions and rest geometry, and integrate an explicitly labelled
strain/distance-based damage proxy. Such a derived measure is not solver stress;
area stretch alone misses area-preserving shear. Output frames are interpolated
between integration states and must not be mistaken for exact substep state.
Damage authoring and calibration are not implemented by this audit.

UV material orientation is available: the builder derives warp/weft hinge
direction from UV edges and rejects unusable UV direction for anisotropic
bending. A host-derived deformation gradient in a valid rest/UV basis could
distinguish directions. There is no official directional fracture law or
per-element stress output, and bend-warp/weft is not a tear-strength model.

## Output and recovery implications

Official vertex output and `VertexMap` describe built ordering. CNX PC2 stores
one constant vertex count and XYZ samples; `StreamingPc2Writer` checks `(N,3)`
every frame. A pre-split, fixed-index connection release can retain PC2 if the
render mesh uses that exact authored topology/order. Runtime vertex duplication,
element deletion or changing faces requires topology samples and identity/UV
mapping; PC2 cannot express those even if vertex count happens to stay constant.
Blender Alembic or a mesh-sequence format could carry topology-changing output,
but the official solver first needs to emit it. No new cache format was added.

A future breakable-connection cache must checkpoint failed-row state and damage
at the same solver boundary, retain row identity/order, and replay the correct
strengths before resume. Cancellation between save/build/resume needs atomic
recovery metadata. Start Fresh must reset failures. Existing safe deletion,
project ownership, PC2 lifecycle and long-path behavior were not changed.

## Real CUDA experiments

Reproducible probe: `tools/audit_gaia_stitch_release.py`, run with official
bundle Python from the bundle root, serially. It uses public frontend APIs,
normal CUDA validation and ordinary scene input files. Two panels are one
eight-vertex/four-triangle shell object, with two independent stitch rows.
There is no source patch, memory injection, private scene mutation or bypass.

The successful release run stops at frames 10 and 20, saves, zeroes one row per
boundary, rebuilds with `preserve_output=True`, resumes, and continues through
frame 35. Each checkpoint's SHA-256 and the corresponding output pose survive
the rebuild byte-for-byte; all output positions remain finite. This verifies
sequential release and motion continuity in a minimal fixture. It does not
certify arbitrary tearing, stress-driven fracture, a coincident strip or
contact-heavy progressive tears.

Initial attempts failed synchronization assertions. Investigation found public
auto-resume clears a pending hold on its second start, and save-and-quit writes
`finished.txt`, which a resumed `run_until_frame` may observe before startup
clears it. The probe explicitly resumes and verifies the actual held marker.
A control launched concurrently failed before its first hold; probes must run
serially because frontend process/busy detection is global. These failures
were not counted as physical passes.

Local reports are `dist/gaia-ripping-stitch-release.json` and
`dist/gaia-ripping-stitch-control.json`. Both serial runs passed. Their output
positions match exactly through frame 10 (maximum absolute difference 0).
After release, the maximum coordinate difference is 0.0285527 m. Final seam
endpoint distances are 0.128730 / 0.196635 m for the released rows versus
0.130217 / 0.158443 m in the intact-stitch control. The norm of position change
from frame 21 through 35 is 0.224902 m in the released run. These are mechanical
trajectory differences, not visual judgment. The panels initially accelerate
toward each other; this fixture does not assert an opening crack under tensile
loading. Checkpoint retention plus the source resume path establish continuity
of stored positions/momentum, not identical transient contact history.
Full CNX regression and production Blender tearing tests are not run for an
audit-only change.

## Exact blocker and recommended next step

Arbitrary fabric Ripping needs an official state-preserving shell connectivity
mutation or native failure mechanism that updates mechanical adjacency, device
tables, contact structures and output identity. 0.23 supplies none of these
through its supported live interface. Stop production Ripping.

The next legitimate CNX feature candidate is **GAIA Breakable Connections**:
keep prebuilt stitch rows/endpoints, evaluate a bounded geometric damage proxy
at held boundaries, checkpoint, update selected row strengths to zero through
`set_stitch`, rebuild preserving output, and resume. Before implementing that
product, require controlled momentum/trajectory tests, collision-heavy fixtures,
plasticity resume, attachment-type coverage, timing/performance measurements and
transactional cancellation/recovery tests. Evaluate a predefined sewn tear-line
mode separately only after those gates; do not label it arbitrary fabric tearing.

External solver source/frontend/binaries/protocol/schema were not modified.
After the experiments, all 319 non-bytecode files under the isolated bundle's
`frontend` and `crates` matched the original official bundle byte-for-byte.
The CUDA solver executable matched the original SHA-256
`42faebb7eefe142c872e6f05af8e780a798a5b65f3743b330d1680cb7b7100a0`.
The separate upstream source checkout remained clean. Probe Python compilation
and `git diff --check` passed.
No production Ripping code, topology cache, add-on release or publication was
created. All existing prerequisite work was preserved.
