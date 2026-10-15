# GAIA 0.23 investigation — sewing capability proved, lifecycle gate open

Current Windows ownership/lifecycle and native Blender long-path findings are
recorded in [GAIA_023_LIFECYCLE_RESULTS.md](GAIA_023_LIFECYCLE_RESULTS.md).
That report supersedes the historical lifecycle blocker descriptions below.
Final regression is 2045 passed, 19 existing skips, 3 deselected. All available
real Windows lifecycle and solver-switch gates pass in unchanged isolated
runtimes; the direct-AppData execution restriction and unavailable Lumen gate
are explicitly retained in that report. No external solver is patched.

## Current evidence (supersedes the historical investigation below)

The initial intra-sewing incompatibility finding below was incomplete: the
documented public `FixedScene.set_stitch(ind, w, stiffness=Array)` API accepts
independent per-row strengths. The required sewing capability is **proved**;
decision A applies. This is not decision B and does not require an upstream
source modification. Full production compatibility is **not yet certified**.

### Verified official runtime

With the user's explicit approval, the official Windows archive was downloaded
separately outside this repository. Actual bytes: **405398963**; actual SHA-256:
`fd3f43aa7e9849526b5113da558443a074039231ba700edfc953b6c83eb990d4`.
The CUDA server reports package 0.1.0, protocol 0.23, schema 2, and detects the
NVIDIA RTX 4070 SUPER. Its bundled Python imports the official frontend.
CPU/CUDA/ROCm backend paths were enumerated. Linux metadata is pinned below,
but Linux download/runtime verification has not been performed.

The separate official source, frontend, and binaries remain unmodified. No
private-state mutation, protocol change, debug validation bypass, copied solver
source, or custom solver build was used. No installer registry selection was
changed; no add-on version, release, commit, or publication was created.

### Stitch contract and real proof

- Native `Object.stitch` has one asset and initially one scalar object strength.
  Native four-column rows expand to six slots. Public native six-column assets
  also exist, but the uploaded typed scene object accepts four-column rows.
- Public cross-stitch entries retain independent strengths and reject a
  same-object pair. Their decoder validation, including SOLID surface projection,
  remains upstream-owned; Cloth NeXt must not silently drop invalid entries.
- Public `FixedScene.set_stitch` plus public `Session.build` exports `stitch_ind`
  as uint64 (M,6), `stitch_w` as float32 (M,6), and `stitch_stiffness` as
  float32 (M,). The real solver consumes those independent strengths.
- Material/group maps or altered barycentric weights are not substitutes for
  per-row strength. Changing weights changes authored geometry/energy.

The real public-API prototype uses one authored cloth object, 16 vertices,
eight triangles, and two intra seams with normalized strengths 0.2/0.9
(raw 100/450). Both are active, vertices remain finite and free, and a
measurable gap difference exceeds 5 mm. Save/load and a second real CUDA bake
retain the exact arrays. The first low-mass fixture did not distinguish the
strengths reliably; it failed the assertion and was retained. An appropriate
high-mass fixture subsequently passed without changing strengths or thresholds.

Real TCMD upload/build/simulation with the same seam contract also passed.
The central CNX converter and binding worker then passed that isolated real
TCMD test. Reports (ignored local artifacts):
`dist/gaia-023-public-stitch-prototype.json`,
`dist/gaia-023-server-stitches.json`, and
`dist/gaia-023-central-bridge.json`.
These isolated proofs are not a pass for the complete production session.

### CNX implementation and outstanding gate

The new central adapter prepares native intra assets while retaining original
UUIDs, mesh topology, row order, legacy loose edges, and each seam's strength.
After the official build, a CNX-owned worker uses public `App.recover`, public
vertex mapping, public `set_stitch`, and public session rebuilding. Independent
strengths are included in recovery identity. Cross-object entries remain owned
by the official decoder. Painted friction uses the existing official triangle
input ABI, not frontend patches; its complete real regression remains open.

The compatibility manifest adds distinct `ppf-0.23-gaia` / **Gaia 0.23**, with
a no-overlay recipe, while preserving the old Gaia, Lumen, and Velune entries
and the 0.22 default. Parsed status preserves successful exemptions and unknown
additive fields separately from errors. The frontend alone generates
`start_link.bin`; CNX does not fabricate contact exemptions.

**Production lifecycle blocker:** with debug root overrides disabled, normal
server path resolution must coexist with CNX's strict recovery-owned directory.
The current isolated project-link approach uploads successfully, but Windows
directory creation through the link fails with `WinError 183` at the official
`export_fixed` session-directory creation. It reproduces with both bundled and
host Python, and with workspace and AppData destinations. Ordinary unlinked
official projects work. This evidence does not establish whether Windows,
the execution environment, or the linking approach is responsible.
Precreating `.cash` only moves the failure and is not a complete solution.
Do not enable the upstream debug root override or weaken recovery ownership
to conceal it. This path implementation remains experimental.

The production >=30-frame cloth/collider/gravity run, playback, cleanup,
restart/second bake, cancellation/reconnect, side-by-side switching, complete
sewing/attachment/SOLID matrix, and headless Blender regression remain open.
Do not advertise or release the unfinished integration as fully compatible.

### Baseline and regression

Generated Companion artifacts were moved recoverably from the extension source
to ignored `dist/gaia-stitch-audit-baseline/`, following the intended test
workflow. The validator was not weakened. Full isolated baseline:
**2003 passed, 19 skipped, 3 deselected** (131.19 seconds).
The initial integration regression failures were fixed; all 37 previously
failing tests passed on rerun. A full follow-up reached 2014 passing tests with
one repeated Companion icon overwrite error. The affected real asset-generation
tests now build in temporary directories with all deterministic-content, image,
color, alpha, and ICO assertions retained. Fresh full rerun:
**2015 passed, 19 skipped, 3 deselected** (128.54 seconds).
Existing prerequisites account for skips; no new skip was added. In particular,
12 real integration tests had no configured executable in this suite; the
separate explicit real CUDA prototype runs above are not counted as those tests.

## Historical source investigation (initial conclusions, not current gates)

Date: 2026-09-30. This is a source investigation and baseline report, not a
certificate of an integrated or tested new engine. No release/version change,
publication, registry migration, or installed-solver replacement was performed.

## Exact source identities

| Source | Tag | Commit | Wire / CBOR |
| --- | --- | --- | --- |
| Currently supported GAIA | `2026-09-21-21-32` | `85e212db8438829e003540649fd7a118cfc5913d` | 0.22 / 2 |
| Requested GAIA | `2026-09-27-20-44` | `344bcb906203db19f3ea59ccd011d5a297f90390` | 0.23 / 2 |
| Corresponding upstream add-on | `addon-2026-09-27-2158` | `344bcb906203db19f3ea59ccd011d5a297f90390` | manifest 1.0.16 |
| Cloth NeXt baseline tested | existing branch HEAD | `7be4f13be1271d1a937cfea289819a17e0c37d71` | existing manifest unchanged |

Upstream was inspected in a separate sibling checkout, not copied into this
repository. The comparison covers wire/state/build changes, format contracts,
frontend decoder/export/session APIs, contact and force-field kernels, add-on
encoding/cache behavior, focused upstream regressions, examples, and workflow
documentation. Archive/runtime verification and remaining implementation work
are still outstanding. The raw source diff contains 318 changed files; many are
documentation moves/translations, not new wire contracts.

Primary source anchors (all links use the exact requested revision):

- [Wire version and historical contracts](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/blender_addon/protocol_version.toml)
- [Frontend scene building and export](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/frontend/_scene_.py)
- [Frontend decoder](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/frontend/_decoder_.py)
- [Native stitch attachment](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/frontend/_scene_object_.py)
- [Contact pair filter](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/kernels/contact/pair_filter.kernel.cpp)
- [Status shape](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-server/src/response/shape.rs)
- [Material / intersection documentation](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/docs/blender_addon/workflow/params/material.md)
- [Force-field documentation](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/docs/blender_addon/workflow/params/force_fields.md)
- [Official release](https://github.com/st-tech/ppf-contact-solver/releases/tag/2026-09-27-20-44)

## Initial compatibility finding: existing Intra-Sewing (resolved)

Cloth NeXt `cloth_next/blender/sewing.py:snapshot_enabled` deliberately exports
both intra- and cross-object Sewing as six-slot `cross_stitch` entries, with a
separate `stitch_stiffness` for each seam. This fixed the previously lost
per-seam Strength and preserves live simulated endpoints rather than pins.
An intra seam has identical source and target export UUIDs.

The new decoder resolves every entry, then calls `Scene.cross_stitch` instead
of appending directly to its internal list. At upstream `_scene_.py:2547`, this
API explicitly rejects `source == target`, directing callers to `Object.stitch`.
Therefore the current Cloth NeXt Intra-Sewing payload is rejected at build by
the requested official frontend. This is independent of the handshake bump.

The documented `Object.stitch` path attaches one stitch asset per object.
`Scene._pack_dyn_input` supplies one scalar `obj.param.get("stitch-stiffness")`
for those rows, whereas cross-stitch entries carry their own scalar. Simply
moving all intra seams into that asset would lose independently authored seam
strengths on an object. Silently sharing strengths, dropping seams, splitting
mesh identity, treating endpoints as pins, or removing upstream validation is
not an acceptable compatibility fix.

The later public `FixedScene.set_stitch` investigation and real prototypes
resolved this finding, as recorded above. A wire-only scalar asset is not the
complete official API surface. No external solver source modification is
authorized or necessary for the proved stitch capability.

## Verified contract delta

1. **Wire 0.23, CBOR schema still 2.** TCMD framing and the existing status enum
   remain unchanged. Strict protocol equality is required; an old server must
   not receive the new object parameter and fail only at scene build.
2. **Existing-intersection allowance.** `allow-existing-intersection` is a scalar
   or object-UUID dictionary in group parameters, default off. Scene build
   resolves it onto dynamic vertices. Either opted-in side can authorize an
   intersecting or contact-offset-proximity pair found at build.
3. **Generated contact links, not geometric repair.** The frontend writes
   optional `bin/start_link.bin`, flat uint32 pairs in the combined namespace:
   dynamic vertices followed by static collision-mesh vertices. The solver
   rejects odd lengths and out-of-range indices, builds its device adjacency,
   and filters those pairs consistently from contact, CCD, and intersection
   reporting for the whole run. Vertex granularity extends coverage to faces
   sharing a corner. Cloth NeXt should not fabricate or regenerate this file;
   it is the authoritative upstream build output.
4. **Exemptions status lifecycle.** Successful build writes
   `build_exemptions.json`; status adds `exemptions`, with records such as
   `{type: existing_intersection, count: N, pairs: [{a: [...], b: [...]}]}`.
   Geometry is world-space triangles/rod edges and the preview is capped;
   `count` is not necessarily the preview length. Build start/failure/cancel
   clear the state; project reselection/reconnect reload successful metadata.
   Exemptions are successful allowances, not errors or violations. Preserve
   records and unknown additive fields separately from error state.
5. **Unsupported allowance cases.** SAND rejects the option by name. Invisible
   walls/spheres are not exempted. A purely static collision mesh has no dynamic
   vertex opt-in of its own; a dynamic counterpart can opt in. New pairs outside
   the fixed links retain normal contact and can still stop on an intersection.
   The upstream blue start-frame overlay is presentation, not required CNX UI.
6. **Inter-group policy.** New `allow-inter-group-intersection` and optional
   `group_vert.bin`; group identity derives from group parameter labels. Static
   collision meshes count as another group. This is distinct from inter-object
   allowances and from start-pair exemptions.
7. **Force fields.** Optional opaque `force_field` parameter data, sampled
   compressed f32 grids, restricted compiled scripts, group targeting and
   per-object `force-field-weight`. The server adds `force_field_check` using
   the same frontend interpreter and compiler (bounded input and timeout).
   New host/device acceleration and air-velocity kernels; these are not an
   instruction to expose authoring controls in Cloth NeXt now.
8. **Stricter stitches / SOLID pins.** Every cross-stitch entry is applied or
   rejected, not silently dropped. SOLID endpoint anchors are projected onto
   tetrahedral surfaces; malformed/missing anchors fail by object name.
   Four- and six-column native stitch assets are supported. SciPy and valid
   Poisson/interior solves are required instead of surface-only fallback.
   SOLID pin rests use solver/world space, correcting transformed-body fields.
9. **Rest tracking and spins.** Tracking pins must cover the complete object
   and have operations; tracking together with plasticity is refused. Public
   pin setters/group IDs and notebook parity APIs were added. Negative spin
   velocities now rotate rather than being discarded by an angle-sign check.
10. **Frame stepping / live inputs.** New optional `hold_at_frame`, `held`, and
    `inputs_updated` sentinels. A held solver retains its device; timeout saves
    and quits. Live field/schedule reload is supported, but changing collision
    windows mid-run is rejected. Server launch removes stale hold sentinels.
11. **Inspection / authoring correctness.** Python statistics accessors and
    script/API helpers were added. Add-on capture/cache evaluation consistently
    hides display-only modifiers and respects the cache/deformer boundary.
    Unsupported/unsampled authoring is refused instead of ignored; STATIC
    varying materials, incompatible rest tracking, SAND radii, and invalid
    seams are covered by new regressions. The upstream UI withdraws the old
    stable-NeoHookean SHELL selection; CNX currently emits baraff-witkin/arap.
12. **Backends.** Native CPU/CUDA/ROCm/Metal implementation changes include
    shared pair-filter and force-field kernels. Python automatic resolution's
    functional contract is unchanged (its diff changes API guidance text).
    Protocol-0.22 backend status fields remain essential in 0.23. Actual
    archive contents, dependency loading, probes, and CUDA launch remain
    unverified until the official asset is downloaded and exercised.

## Capability matrix for subsequent GAIA planning

“Present in 0.22” means observed in the old source/contract, not a guessed first
release. Historical protocol gates are listed only when documented upstream.
Future phase labels are planning categories, not a new product commitment.

| Capability | Upstream support / delta | Verified contract requirement | Current Cloth NeXt support | Future work category |
| --- | --- | --- | --- | --- |
| Existing-intersection exemptions | New build links and status records | 0.23 / schema 2 | No opt-in UI or retained parsed exemptions yet | Diagnostics, then collision authoring |
| Self/inter-object allowances | Present in 0.22 | Audited 0.22/2; 0.23 adds related fields | General contact toggle, not these per-object modes | Collision policy |
| Inter-group allowance | New policy bit and group map | Requested 0.23/2 contract | Not exposed | Collision groups |
| Sampled Force/Wind/Vortex/Turbulence fields | New grids, targeting, device kernels | Requested 0.23/2 contract | Existing scene/dynamic force-to-gravity/wind path is not this new field system | Fields authoring |
| Scriptable fields and noise | New restricted compiler, check request, device evaluation | Requested 0.23/2 contract | Not exposed | Advanced fields / scripting |
| Spatial material maps | Present in 0.22, SOLID refusal/solve improvements | 0.20 contract documented; audited 0.22/2 and 0.23/2 | Existing face friction override only; not general material maps | Material-map authoring |
| Animated material sliders/maps | Present in 0.22 | 0.20 maps/time/units contract | Animated scene forces; no general animated material-map export | Animated materials |
| Stretch plasticity | Present in 0.22 | Audited 0.22/2 and 0.23/2 | Cloth and Soft Body parameters exported | Regression, no new UI |
| Bend plasticity | Present in 0.22 | Audited 0.22/2 and 0.23/2 | Cloth and cable parameters exported | Regression, no new UI |
| Anisotropic bending | Present in 0.22, bend-warp/bend-weft | Audited 0.22/2 and 0.23/2 | Not exported | Advanced material controls |
| Translation locks | Present in 0.22 | 0.14 axis; 0.19 all-axes contract | Not exported | Motion constraints |
| Rotation locks / axis modes | Present in 0.22 | 0.15 axis, 0.16 prohibit-axis, 0.19 all-axes | Not exported | Motion constraints |
| Collision windows | Present in 0.22; held reload refuses changes | Audited 0.22/2 and 0.23/2 | Not exported by dynamic whitelist | Timed collision policy |
| Solver per-object statistics | Existing CBOR output, new Python inspection APIs | 0.17 documented output contract | Runtime summary telemetry, not per-object statistics archive/UI | Diagnostics / analysis |
| PDRD rigid bodies | Present in 0.22 | Audited 0.22/2 and 0.23/2 | Rigid Body export and safe quality presets | Regression; advanced joints deferred |
| SAND | Present in 0.22; new explicit refusals | Audited 0.22/2 and 0.23/2 | Not an exported role | Granular simulation |
| ROD | Present in 0.22 | Audited 0.22/2 and 0.23/2 | Cable/Rope export | Regression; advanced rod maps deferred |
| SOLID | Present in 0.22; pin field/stitch projection changes | Audited 0.22/2 and 0.23/2 | Soft Body tetrahedral export | Critical pin/attachment regression |
| Rest-shape tracking | Existing data; new public API and strict coverage/plasticity guards | Audited 0.22/2 and 0.23/2 | Internal pin configuration field, not general authoring | Rig-driven rest-shape workflow |
| Reference rest bending | Present in 0.22 | 0.09 documented optional data contract | Initial-geometry bend rest exposed; no independent reference mesh workflow | Reference-shape authoring |
| Deforming STATIC colliders | Present in 0.22; capture/cache correctness changes | 0.06 deforming data, 0.07 output-map contract | Evaluated/captured animated colliders | Critical existing-workflow regression |
| Angular velocity overwrite | Present in 0.22; negative spin fix | 0.09 documented; audited 0.22/2 and 0.23/2 | World-space angular override exported | Regression; principal-axis authoring deferred |
| Object Attachments / cross Sewing | Existing six-column payload, stricter validation | Audited 0.22/2 and changed 0.23/2 | Explicit per-attachment/seam strengths | Critical existing-workflow regression |
| Intra Sewing with per-seam strength | Public cross API rejects same object; public FixedScene setter supports row strengths | Native intra asset plus public per-row setter, real CUDA proof above | Central migration implemented, production lifecycle gate open | Required existing workflow |
| Backend identity | Present in 0.22 and retained | 0.22 solver_target_dir/solver_backend; strict 0.23 handshake | Registry, executable probes, target/backend verification | Infrastructure validation |
| Crash diagnostics | Existing error/crash_kind and sidecars | 0.18 crash_kind; 0.23 adds successful exemptions | Structured failure and reconnect diagnostics | Retain additive state; no error conflation |
| Debug overlays | New start-pair visualization and expanded scenarios | Exemptions geometry in 0.23 status | Attachment/sewing wire overlays and violation preview | Start-exemptions preview deferred |
| Held frame/live-input editing | New sentinels and Python APIs | 0.23 release implementation; no new required bake request | Not exposed | Interactive simulation, deferred |

## Official asset metadata (Windows now verified above)

Official release API reports the Windows x64 archive
`ppf-contact-solver-2026-09-27-20-44-win64.zip` as 405398963 bytes with SHA-256
`fd3f43aa7e9849526b5113da558443a074039231ba700edfc953b6c83eb990d4`.
The Linux x64 tarball is 646442979 bytes with SHA-256
`cec7c8e6ad47f1040fbbe4c84869bde2dad82e4721180be258daa7f3f6fd9f91`.
The Windows bytes, layout, imports, probe, and isolated CUDA runs have since
been verified as recorded above. The Linux values remain host metadata only.

## Baseline quality results and remaining work

Unmodified baseline `python -m pytest`: **2002 passed, 1 failed, 19 skipped,
3 deselected**, 127.99 seconds. The failure is
`test_package_structure.py::test_extension_source_root`: existing generated
Companion build artifacts are present under the source extension root, which
the source validator correctly rejects. Those ignored build files were not
deleted or the validator weakened. External integration tests lacked an
explicit configured executable in this run; platform/artifact skips are
reported by their existing prerequisite gates, not added for this task.

The current implementation and outstanding gates are listed at the top of
this report. No new Cloth NeXt commit has been created. Existing installations,
registry selections, and default engine remain intact. Do not apply the old
intersection overlay to the new engine or publish until all gates pass.
