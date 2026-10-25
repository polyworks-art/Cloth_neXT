# TETHYS Dynamic Wetness

TETHYS simulates the water that stays **on** and **inside** a baked cloth.
GAIA (with FLIP Fluids) handles the cloth's interaction with environmental
water; TETHYS runs afterwards on the finished cloth cache and the prepared
GAIA water field. It is one-way: FLIP/GAIA → TETHYS. Nothing is fed back to
FLIP, the cloth simulation or PPF.

Status: implemented and tested on branch `claude/project-thread-5ybzhf`.
Not released. Version and update channels are unchanged.

## 1. Architecture

```
Blender extension (GPL, Python)          Bundled TETHYS Engine (native, proprietary)
  UI, profiles, Material Zones             surface topology and adjacency
  reads PC2 + .gaia, crops water   --->    geometry per frame / substep
  writes request + water stream            contact interpretation, injection
  owns process, progress, cancel   <---    transport, absorption, wicking,
  authenticates cache, EXR frames          evaporation, drainage, ledger
  material nodes, debug overlay            writes the wetness cache
```

| Part | Location | Responsibility |
|---|---|---|
| `cloth_next/tethys/profiles.py` | add-on | Wetness profiles, Material Zone resolution to per-triangle material rows |
| `cloth_next/tethys/protocol.py` | add-on | Binary request, water stream, cache reader (protocol v1) |
| `cloth_next/tethys/engine.py` | add-on | Locate and authenticate the bundled binary, GAIA gate, one owned process |
| `cloth_next/tethys/contact_export.py` | add-on | Crop `.gaia` frames around the cloth's PC2 bounds |
| `cloth_next/tethys/cache.py` | add-on | Owned cache files, sidecar, inspection, safe deletion |
| `cloth_next/blender/tethys_*.py` | add-on | Properties, bake operator, materials, UI, overlay |
| `TETHYS Engine.exe` / `tethys-engine` | `bin/` in the extension | The numerical solver, built from the private repository `polyworks-art/THETYS` |

The add-on contains no transport, absorption, wicking, drying or contact
algorithm. `tests/test_tethys.py::test_distributed_python_contains_no_tethys_solver`
checks the module set and the absence of solver code and native sources.

**Process ownership.** The engine is a short-lived child of Blender, started
only by **Bake Wetness**. It runs inside the same kill-on-close ownership
boundary as solver processes (`ppf/process.py`: Windows Job Object, POSIX
session), so closing Blender stops it. The Companion is not started for
TETHYS: no second Companion instance can appear. Progress shows in Blender's
status bar and the panel. Running the Bake window for TETHYS needs a new
Companion mode and is deferred (its visual style is unchanged).

**Availability.** TETHYS can start only when the selected solver engine is
GAIA, the installation is healthy, and GAIA access is granted (existing
`updater.engines` selection and `gaia_account.access_granted()`). Otherwise
the panel names the reason, Bake Wetness is disabled and the engine never
launches. Existing wetness caches stay untouched when GAIA is deselected.
TETHYS enablement (per object) and GAIA availability are separate.

**Native authorization (enforced).** The engine itself reads the PolyWorks
`installation-id` and `gaia-entitlement` files and verifies an
installation-bound, Ed25519-signed GAIA entitlement (compiled-in key `k1`;
`typ polyworks.feature-entitlement`, `v 1`, `prd cloth-next`, installation,
validity window, `feat:["gaia"]`). A licence entitlement or a mere GAIA
installation does not qualify. Without a valid token it refuses the bake with
`TETHYS-E140` before reading any input (`kEnforceGaiaEntitlement = true`, a
compile-time constant). The add-on fetches and renews the token through
`POST /v1/account/gaia-entitlement` whenever it checks GAIA access and deletes
it when access is revoked, the session is rejected or the account is
suspended. Verified live against production on 2026-10-25 (issuance, renewal,
`valid` in the engine, revocation → token removed → `missing`). Offline
window after revocation: until the token's `exp` (7 days) + 5 min.
Contract: `docs/polyworks/GAIA_ENTITLEMENT_SERVER_REQUIREMENTS.md`.

## 2. Surface representation

* One cell per **loop triangle** of the cloth mesh (quads/N-gons give 2+ cells,
  all owned by their source polygon). The PPF mesh is never modified or
  subdivided.
* Neighbours are triangles that share **both vertices of an edge**. Overlapping
  but disconnected sheets therefore never exchange water. UV seams do not
  split vertices and never interrupt transport. Edges shared by three or more
  triangles connect every pair with weight `1/(n-1)`.
* Open edges (one triangle) are drainage boundaries.
* Geometry (area, normal, centroid, in-plane edge normals, fabric direction)
  is recomputed from PC2 positions for every substep, linearly interpolated
  between frames. Cells with near-zero area keep their water but do not take
  part in transport until the deformation restores them (reported per frame).
* Fabric direction for anisotropy is the surface direction of increasing U of
  the active render UV map. Without UVs, transport is isotropic and the
  missing-direction count is reported.
* **Surface Detail** (1, 2, 4, 6, 8; protocol 2) splits every cloth
  triangle into level² virtual sub-cells on a barycentric lattice inside the
  engine. Lattice points on a shared cloth edge are shared by both
  neighbours, so transport crosses cloth edges as before and disconnected
  sheets stay disconnected; sub-cells inherit polygon and Material Zone and
  follow the deforming parent with fixed weights. The PPF mesh is never
  changed. Limit: 4 million sub-cells.
* **Fabric irregularity:** a deterministic value-noise field (Feature Size,
  default 4 mm), evaluated at the first-frame positions so it stays on the
  fabric, scales each cell's retention (×(1 + 0.9·v·η)) and mobility
  (×(1 − 0.6·v·η)) by the material's Surface Variation v. Runoff gathers in
  the slick, low-retention paths: streaks and fingered wetting fronts.

## 3. State, units, equations

Per cell: surface water `S` and absorbed moisture `A`, both volumes in m³.
Depth `h = S/area`, saturation `s = A/(c·area)` with capacity `c` in m.
Because the state is a volume, area changes never create or destroy water;
if compression lowers the capacity below `A`, the excess moves to `S`.

Each substep (adaptive; bounded by Quality: 16/64/256 per frame):

1. **Injection** from contact fraction `f` (0..1): the film relaxes toward
   `f · immersion_film · area` with time constant *Wetting Time*. It never
   exceeds that target, so permanent submersion cannot accumulate water.
2. **Film transport** across each shared edge (lubrication film law):
   `q = ρg/(3μ) · m · h_m³ · (slope + Δ(cosθ·h_m)/d) · L`
   with mobile depth `h_m = max(0, h − retention)`, tangential gravity
   projected on both cells' in-plane edge normals (`slope`), the hydrostatic
   film head on surfaces facing gravity, edge length `L`, centroid distance
   through the edge `d`, and mobility `m` (harmonic mean of the two cells'
   direction-dependent mobilities). The donor is upwind. Submerged film
   (`f`) does not run off. Gravity is the scene/Force gravity vector in world
   space, so rotating cloth changes the flow direction.
2b. **Rivulets**: on a slope, film across the flow direction is pulled from
   the thinner into the thicker neighbour (contact-line contraction),
   `(0.5 + 1.5·repellency) · slope · (1 − along²)` times the same h³ law, so
   runoff gathers into streaks instead of running as a sheet. Downhill
   exchange, flat cloth and zone borders are unaffected; it is conservative.
3. **Drainage** at open edges with the same law against zero outside head.
   Drained water is a ledger sink and is recorded per polygon (Drainage
   channel) for later drip effects.
3b. **Beads** (repellent fabric, beading = (repellency − 0.35)/0.5 clamped):
   mobile film above the pinned depth gathers into one bead per cell. A bead
   whose footprint (0.9·V^(1/3)) exceeds a fixed per-cell reach of 0.25–0.6
   cell (uneven contact lines) collects neighbouring
   beads and film (coalescence). It moves when gravity beats contact-line
   pinning, V^(2/3)·sin(slope) > 0.155 m · retention · grip, at up to
   0.3 m/s × mobility, one cell at a time toward the most downhill edge;
   it tops up the film of the cell it leaves (trail), merges with beads it
   meets, spreads into wettable or submerged fabric and drips off open edges
   (drainage). Grip (0.6–2.1, skewed high so most beads hold and few run)
   varies deterministically per bead. Beads dry
   through their cap (1.2·V^(2/3)). They are surface water in the ledger.
   This is a simplified, conservative bead model, not surface tension.
4. **Absorption** `S→A`: `k·(1 − r·(1−s))·(1−s)` as an exact exponential
   per step, limited by free capacity (`k` Absorption Rate, `r` Repellency).
5. **Capillary wicking** of `A` driven by saturation differences:
   `Q = D_h · c_h · (s_i − s_j) · L/d`, `D_h`, `c_h` harmonic means.
6. **Evaporation**: the film dries first at
   `ambient_rate × material_rate × Evaporation`; moisture dries in proportion
   to saturation once the film has gone.

Every transfer is computed once, removed from its donor and added to its
receiver. Donor limiting (and receiver capacity limiting for moisture)
keeps every quantity non-negative without deleting water. A remainder below
1e-12 of the donor is reported as `roundoff` in the ledger; anything larger
aborts the bake. After every frame the engine checks

`initial + injected = surface + absorbed + evaporated + drained + roundoff + residual`

with `|residual| ≤ 1e-9 · (initial + injected)` (observed ~1e-17 m³).
Results are deterministic for fixed inputs (single thread, fixed order,
no fused multiply-add). The Windows build (MinGW-w64 g++ 15.2) and the
Linux build (AlmaLinux 8, g++ 13) produce a byte-identical cache for the same
request, and both pass the same numerical test suite. The engine and its tests
also run clean under AddressSanitizer/UBSan (this found and fixed one
use-after-free in the cache writer before release).

## 4. Materials and Material Zones

Wetness profiles are separate from PPF mechanics and never derived from
friction, bending or stretch values. Starting points (not measurements):

| Profile | Absorb 1/s | Capacity mm | Mobility | Repellency | Wicking mm²/s | Drying × | Border perm. | Retention mm | Anisotropy |
|---|---|---|---|---|---|---|---|---|---|
| Cotton | 4.0 | 0.35 | 0.05 | 0.05 | 8 | 1.0 | 1.0 | 0.15 | 1 |
| Wool | 0.4 | 0.30 | 0.03 | 0.5 | 3 | 0.7 | 0.8 | 0.30 | 1 |
| Silk | 1.5 | 0.10 | 0.15 | 0.2 | 5 | 1.2 | 0.9 | 0.05 | 3 |
| Leather (treated) | 0.05 | 0.20 | 0.12 | 0.6 | 0.3 | 0.4 | 0.3 | 0.08 | 1 |
| Waterproof Nylon / Coated | 0.01 | 0.002 | 1.0 | 0.95 | 0.02 | 1.5 | 0.1 | 0.03 | 1 |
| Rubber / Latex | 0 | 0 | 1.5 | 1.0 | 0 | 1.5 | 0 | 0.02 | 1 |
| Absorbent Lining | 8.0 | 0.80 | 0.02 | 0 | 12 | 0.9 | 1.0 | 0.20 | 1 |
| Denim | 2.5 | 0.45 | 0.04 | 0.1 | 6 | 0.8 | 0.9 | 0.20 | 1.6 |
| Synthetic Weave | 0.8 | 0.05 | 0.25 | 0.4 | 4 | 1.3 | 0.8 | 0.06 | 1.2 |
| Generic Fabric | 2.0 | 0.25 | 0.08 | 0.2 | 4 | 1.0 | 1.0 | 0.10 | 1 |

*From Material Preset* maps fabric presets by identity (Cotton, Linen,
Canvas → Cotton; GORE-TEX, X-Pac, Dyneema, Tyvek, laminated cotton → Waterproof;
ripstop, polyester, Lycra, CORDURA → Synthetic; …). Unmapped presets use
Generic Fabric.

Material Zones reuse the existing FACE attribute `cloth_next_material_zone`,
its UUIDs, tokens, topology validation and editing. Each zone has an
optional **TETHYS Wetness Properties** block (profile or Inherit, plus
per-property overrides). Rules:

* A zone override applies only to its faces; a disabled zone, or one without
  wetness changes, inherits the Base Material row. Removing an override
  restores the inherited value.
* Identical rows are merged in the engine, so inheriting zones never create
  a seam (Cotton → Cotton flows normally).
* At an interface between different rows the film flux is scaled by
  `√(perm_A · perm_B)`; entering a more repellent material needs an extra
  head of `1 mm × Δrepellency`. Moisture wicking uses harmonic means, so a
  zero-capacity or zero-wicking side blocks it (Cotton moisture does not
  diffuse into waterproof nylon). The computation depends only on the pair of
  materials and the geometry, not on triangle order or winding.
* Wetness settings are not part of any PPF fingerprint. Editing them never
  marks the cloth simulation dirty or stale; it only makes the wetness cache
  stale.

## 5. Water contact (GAIA / FLIP → TETHYS)

The prepared `.gaia` water field stores, per frame, trilinearly splatted
liquid-particle support (`influence`, 0..1) and velocity. It is particle
support, not a signed distance. TETHYS therefore:

* requires a Fluid Cache built from **all liquid particles** (FLIP Fluid
  Particles export). Surface-only caches mark only the liquid surface shell
  and are refused, because cloth deep under water would read as dry;
* crops each frame to the cloth's PC2 bounds plus two nodes (only cropped
  nodes travel to the engine);
* in the engine samples influence at four points per triangle and maps it
  through a smoothstep between *Contact Start* (0.25) and *Full Contact*
  (0.75); the per-triangle fraction is interpolated linearly in time between
  frames;
* matches frames by Blender frame number; the Fluid Cache FPS must equal the
  scene FPS and it must cover the cloth cache's frame range, else the bake is
  refused.

Water velocity is transported and validated but **not used** by the v1
physics (no splash or impact wetting). No two-way coupling exists.

## 6. Bake, cache, ownership

Bake Wetness (Blender main thread snapshot → background thread):

1. Authenticate the cloth PC2 through its sidecar (READY only). The PC2 is
   only read; its sidecar and file are never written.
2. Validate the GAIA Fluid Cache (sampling, FPS, frame range).
3. Resolve wetness rows and per-triangle ownership.
4. Write the water stream and request into a **staging** stem.
5. Run the engine; it reads the PC2 directly and writes
   `<staging>.tethys.partial`, then renames it atomically.
6. Blender authenticates every frame checksum, writes the EXR preview frames,
   then replaces the previous result and seals the sidecar.

Files in the object's cache folder (same folder as its PC2):

| File | Content |
|---|---|
| `cn_tethys_<id>.tethys` | Header (version, channels, polygons, frame range, FPS, identity JSON), per frame: 16 ledger doubles + float16 channels per polygon, index with SHA-256 per frame, trailer |
| `cn_tethys_<id>.meta.json` | Sidecar: completion state, fingerprints (settings, sources: PC2 SHA-256 + `.gaia` fingerprint + frames, material rows, topology), cache size and SHA-256, layout, ledger, `metadata_digest` |
| `cn_tethys_<id>_frames/*.exr` | Per-frame images, lossless 16-bit ZIP. UV layout: R water height mm, G absorbed mm, plus `_normal_` frames (water normal as normal-map colour). Per face: R surface mm, G absorbed mm, B contact |

Channels: surface water (mm), saturation; with *Debug Channels* also contact
fraction, drained mm per frame and surface flux vector.

* A failed or cancelled bake removes only the staging files; the previous
  wetness result and the cloth cache stay valid.
* Rebake replaces only this object's `cn_tethys_` files. Clear removes them
  and restores the materials; PC2 files are never touched.
* Deletion goes through `core.safe_delete.delete_owned` (contained in the
  cache folder, bounded Windows lock retries).
* Restart is deterministic: a bake always runs from the first frame;
  identical inputs give a byte-identical cache. Mid-bake resume is not
  implemented.
* The cloth Bake and Clear Result are disabled while a wetness bake runs
  (the engine holds the PC2 open).

## 7. Shader integration

**UV layout (default):** the engine rasterises the sub-cells into the
object's render UV map at Wetness Texture size (512–2048): R water height
(mm), G absorbed water (mm), and the tangent-space normal (x, y) of the water
surface; Blender writes the normal as a second, normal-map coloured EXR
sequence (`<stem>_normal_####.exr`). Corner values average only cells of the
same material, so zone borders stay sharp. Everything is texture-only: no
extra geometry is created.

*Drops (rain-on-glass look).* On repellent fabric (beading > 0) the film
does not stay a sheet. Resting beads from the solver join the film, and
the beading share above a 0.02 mm residue collects into close-packed drops
of very different sizes: seeds on three lattices fixed to the fabric
(pitch 2.6 / 1.3 / 0.65 × 0.9 × Feature Size, each lattice cell occupied
with probability 0.55 / 0.7 / 0.85) own the texels nearest to them relative
to their size, and each basin becomes one drop at its water-weighted centre,
so drops shrink in place as the water drains or dries. A small isolated wet
patch (up to 4 coarse seed cells, e.g. a raindrop impact) pulls together
into one drop at its centre instead of a cluster. A drop is a
spherical cap with a contact angle of 70° + 55° × beading; on a slope it
hangs as a teardrop (stretched downhill, round at the bottom, tapered at the
top, more for large drops on steep slopes), using the downhill direction of
the triangle in UV space.

*Sheet breakup.* A repellent area that is no longer submerged does not
turn into drops at once. Round holes open at nuclei fixed to the fabric on
three scales (spacing ~2.5 cm, 1.1 cm, 0.5 cm at the default Feature Size;
sizes strongly randomised, small holes only where the sheet is still
intact), each after its own delay and at its own pace. Holes press against
each other along curved borders (relative depth distance/radius), the
remaining water forms smooth rims and ridges that web out where three holes
meet, hole floors keep a mist of tiny drops. 2.5 s after exposure the
network breaks up and the drops form where the ridges and pools were. Spots
up to ~2 cm across (rain impacts) skip this and become one drop. Drops use
four seed scales with a skewed size spread, a per-drop tilt and stretch and
a wobbly contact line; the whole relief is smoothed over ~0.25 mm within
each material before normals are derived.

*Runners.* Beads that moved this frame are drawn on their own at the end of
a rivulet: every bead hop is drawn along the downhill direction (removing
the cell-to-cell zigzag) with a meander, varying width and swelling, blurred into a smooth
rounded water line, and sweeps a lane in which the small drops are gone.
Rivulets fade with a 6 s time constant, lanes refill with 12 s. Drops never
open up under water (submerged cells keep a sheet). The water normal is the
finite-difference gradient of the height field.

The material reads the sequence through a UV Map node. **Per Face:** one pixel per
polygon, located through the FACE attribute `cnx_tethys_cell` (polygon index
+ 1); this attribute also marks TETHYS objects in both layouts (0 = no data). Blender plays
the sequence itself (Image Texture, Closest, frame offset = bake start), so
scrubbing, Eevee and Cycles viewport, F12, animation and background renders
need no Python per frame, and faces of different zones stay sharp.

For every Principled BSDF in the object's materials the original Base Color,
Roughness (and Coat Weight when unused) sources are captured. Linked sources
(textures) are reconnected into the node group **`CNX TETHYS Wetness`**, whose
outputs feed the BSDF: absorbed water darkens via `mix(c, c·c, darkening·moisture)`
with moisture = absorbed mm / *Moisture Response* (so a waterproof coat with
almost no capacity does not darken), the film lowers roughness toward *Wet
Roughness* and raises Coat Weight. In the UV layout a Normal Map node
(tangent space, render UV map, strength *Water Relief*) turns the engine's
water normal into the shading normal; where water covers the fabric (the
*Film* output) it replaces the fabric's own normal, elsewhere an existing
normal map is kept. Clear restores it. With
zero wetness the outputs equal the inputs. Objects that share the material but
have no TETHYS data stay dry. A material shared by two TETHYS objects is
refused (make it single-user). Clear restores the exact previous links and
values and removes all added nodes and the image.

Writing `cnx_tethys_cell` is not a solver-visible change (positions and
topology untouched); the cloth's validation record is kept.

## 8. UI

Physics → **TETHYS Wetness**, its own panel right after GAIA Water Flow
(header checkbox = Enable TETHYS; switching on needs GAIA). Up front only
what an artist touches: *Fabric* (wetness profile, showing the profile taken
from the cloth preset), the cache status with **Bake Wetness** and a clear
button, and the look (Wetness, Darkening, Wet Roughness, Drop Relief; no
rebake). Collapsed: *Detail* (Texture, Water Cells, Drop Size), *Advanced*
(groups Runoff, Soaking and Drying, Water Contact, Shading Response),
*Fabric Overrides* and *Debug* (Surface Water, Absorbed Moisture,
Saturation, Water Flow Direction, Material Zone Boundaries, Water Contact,
Drainage/Runoff). The debug overlay is a GPU draw
handler that exists only while a preview is active; it reads the cache and
never changes data or fingerprints.

## 9. Measurements (Windows 11, MinGW-w64 build, 48 frames)

`python tools/bench_tethys.py --engine <exe> --output <dir>`: waving,
stretching tilted sheet, rising and receding water, Evaporation 100.

| Vertices | Zones | Engine s | ms/frame | Substeps | Peak RAM | Cache/frame |
|---|---|---|---|---|---|---|
| 5,041 | 1 | 0.15 | 3.3 | 47 | 12.6 MiB | 19.8 KB |
| 5,041 | 4 | 1.07 | 22.9 | 783 | 12.6 MiB | 19.8 KB |
| 19,881 | 1 | 0.61 | 12.9 | 63 | 28.2 MiB | 78.6 KB |
| 19,881 | 4 | 7.19 | 152.9 | 1166 | 28.2 MiB | 78.6 KB |
| 50,176 | 1 | 1.86 | 39.5 | 87 | 60.2 MiB | 199 KB |
| 50,176 | 4 | 20.60 | 438.2 | 1181 | 60.2 MiB | 199 KB |

The zone runs include Waterproof Nylon (20× Cotton's mobility). Its fast
film dominates the step count; most of those runs hit the Quality limit
(longer steps, still positive and conservative). The interface links
themselves are 0.7–2 % of all links. Python preparation (request + water
stream): 0.08–0.8 s. Water stream: 53 MB for 48 frames when the cloth spans
the whole water grid.

Blender E2E (`tools/blender_tethys_e2e.py`, Blender 5.2.3, 625-vertex sheet,
96 frames, real GAIA cloth bake 7 min): wetness bake incl. EXR frames and
material setup 0.63 s, engine 0.04 s.

## 10. Tests

* Engine (private repository): `python build.py --tests` (Windows) and
  `--linux-alma --tests` (Linux): 46 checks covering requirements 1–15 and
  26–30 of the brief, determinism and input validation.
* Add-on: `tests/test_tethys.py` (16 tests; engine round trips need a staged
  engine or `CLOTH_NEXT_TETHYS_ENGINE`): profiles and zone resolution,
  no solver in Python, GAIA gate, binary authentication, engine round trips
  (zones reach the right cells, conservation, determinism, protocol mismatch,
  cancel, corrupt cache, no-water), sidecar authentication and owned removal,
  water cropping and source refusal, packaged-engine validation.
* Blender: `tools/blender_tethys_e2e.py` (35 checks, see section 9).

## 11. Not implemented / limitations

* Beads are a simplified model (pinning against gravity, cell-to-cell
  motion); no surface tension, contact-angle dynamics, airborne drops,
  splash wetting or two-way FLIP coupling.
  No reactive PPF material physics (wet cloth weight/cling is GAIA's own
  Wet Fabric option, unrelated to TETHYS).
* Overlay debug views remain per polygon (the material shows full detail).
* Cloth objects with animated transform, parent or constraints are refused.
* Water velocity is not used by the physics.
* Bake window (Companion) progress for TETHYS, and mid-bake resume.
* Sewing seams (stitches between panels) do not transport water.
* The Linux binary needs glibc 2.25 or newer (built on AlmaLinux 8 with a
  static C++ runtime); it was run under WSL, not yet on a native Linux desktop.
* Profile values are artist starting points, not calibrated measurements.

## 12. Packaging and licensing (needs review)

`tools/stage_tethys.py <binary>` stages the prebuilt engine as
`bin/TETHYS Engine.exe` (Windows) or `bin/tethys-engine` (Linux) with
`tethys_manifest.json` (size, SHA-256, protocol). At runtime the binary must
match the manifest, the platform and the protocol, or TETHYS is unavailable.
`tools/validate_extension.py` rejects a staged engine in the source tree and,
in a packaged ZIP, accepts it only as one binary for the ZIP's platform that
matches its manifest (executable mode on Linux). Developers point
`CLOTH_NEXT_TETHYS_ENGINE` at a local build instead of staging into the source.
Release builds: the prebuilt engine binaries (production builds only, no
source, no test key) are vendored in `vendor/tethys/<platform>/` with
`vendor/tethys/ENGINE.json` (engine commit, sizes, SHA-256, protocol).
`build-release-candidate.yml`, `publish-dev.yml` and `ci.yml` run
`stage_tethys.py` next to `stage_companion.py`, so every packaged extension
contains exactly one engine for its platform; `scan_release_artifact.py`
allows exactly `bin/TETHYS Engine.exe` and `bin/tethys-engine` besides the
Companion. After an engine change: build it in `polyworks-art/THETYS`
(`python build.py --tests`, `python build.py --linux-alma --tests`), then run
`python tools/update_tethys_vendor.py ../TETHYS-Engine` here and commit.
Both engine builds are byte-reproducible. A local Windows package built this
way passed Blender's extension validation, `validate_extension --phase
packaged`, the artifact scan and the built-artifact tests.

Open legal questions (no compliance claim made here):

1. The add-on is GPL-3.0-or-later; the engine is proprietary. They are
   separate programs that communicate through files and a command line, and
   the engine contains no GPL code (it was written independently; Blender
   Watercolor was read for concepts only). Whether this “aggregate” reading
   holds, and how to word the notice in the extension, should be confirmed.
2. Code from other Cloth NeXt contributors must not enter the engine without
   their agreement.
3. Distribution channels (Superhive, the Cloth NeXt server) may have their
   own rules for proprietary binaries inside GPL add-ons; the Blender
   Extensions platform does not accept them.
