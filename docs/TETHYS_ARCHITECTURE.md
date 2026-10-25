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

**Native authorization (prepared, not enforced).** The engine itself reads
the PolyWorks `installation-id` and `gaia-entitlement` files and verifies an
installation-bound, Ed25519-signed GAIA feature entitlement (compiled-in key
`k1`; type, version, `purpose: "gaia-access"`, installation, validity window, `feat:["gaia"]`).
A licence entitlement or a mere GAIA installation does not qualify. The add-on
fetches and renews the token whenever it checks GAIA access and deletes it
when access is revoked or the account suspended. Enforcement is compiled off
(`kEnforceGaiaEntitlement = false`) until the server issues these tokens for
every access path; until then a missing token blocks nothing and the verdict
is only reported (start event, wetness sidecar). Server contract and the
offline window after revocation (at most 7 days + 5 min):
`docs/polyworks/GAIA_ENTITLEMENT_SERVER_REQUIREMENTS.md`.

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
* Virtual refinement below the PPF resolution is **not implemented**. Display
  resolution is one value per polygon.

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
3. **Drainage** at open edges with the same law against zero outside head.
   Drained water is a ledger sink and is recorded per polygon (Drainage
   channel) for later drip effects. No droplets are created.
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
| `cn_tethys_<id>_frames/*.exr` | Per-frame preview images (R surface mm, G saturation, B contact) |

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

Each polygon owns one pixel of the object's EXR sequence. A FACE attribute
`cnx_tethys_cell` (polygon index + 1, 0 = no data) locates it. Blender plays
the sequence itself (Image Texture, Closest, frame offset = bake start), so
scrubbing, Eevee and Cycles viewport, F12, animation and background renders
need no Python per frame, and faces of different zones stay sharp.

For every Principled BSDF in the object's materials the original Base Color,
Roughness (and Coat Weight when unused) sources are captured. Linked sources
(textures) are reconnected into the node group **`CNX TETHYS Wetness`**, whose
outputs feed the BSDF: moisture darkens via `mix(c, c·c, darkening·moisture)`,
the film lowers roughness toward *Wet Roughness* and raises Coat Weight. With
zero wetness the outputs equal the inputs. Objects that share the material but
have no TETHYS data stay dry. A material shared by two TETHYS objects is
refused (make it single-user). Clear restores the exact previous links and
values and removes all added nodes and the image.

Writing `cnx_tethys_cell` is not a solver-visible change (positions and
topology untouched); the cloth's validation record is kept.

## 8. UI

Physics → Cloth NeXt → **GAIA Water Flow → TETHYS Dynamic Wetness**
(header checkbox = Enable TETHYS): Wetness Profile and overrides, *Surface
Flow*, *Absorption and Drying*, *Wetness Cache* (Bake Wetness, Clear Wetness
Cache, status), *Wet Appearance*, *Debug Visualization* (Surface Water,
Absorbed Moisture, Saturation, Water Flow Direction, Material Zone
Boundaries, Water Contact, Drainage/Runoff). The debug overlay is a GPU draw
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

* No droplets, surface tension model, splash wetting or two-way FLIP coupling.
  No reactive PPF material physics (wet cloth weight/cling is GAIA's own
  Wet Fabric option, unrelated to TETHYS).
* Virtual refinement below the cloth resolution; display is per polygon.
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
Release CI is **not changed yet**: it needs a step that fetches the engine
binary for the target platform from the private build and runs
`stage_tethys.py` before packaging. Until then released builds simply show
“The TETHYS Engine is not included in this Cloth NeXt build”.

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
