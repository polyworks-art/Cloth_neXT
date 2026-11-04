<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/cloth-next-white.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/cloth_next_icons/cloth_next.svg">
    <img src="assets/cloth-next-white.svg" alt="Cloth NeXt Logo" width="240">
  </picture>
</p>

<h1 align="center">Cloth NeXt</h1>

<p align="center">
  <a href="https://github.com/polyworks-art/Cloth_neXT/releases/tag/2.0.0"><img alt="Release 2.0.0" src="https://img.shields.io/badge/release-2.0.0-303030"></a>
  <img alt="Blender 5.0+" src="https://img.shields.io/badge/Blender-5.0%2B-f5792a">
  <img alt="Windows x64" src="https://img.shields.io/badge/platform-Windows%20x64-303030">
  <img alt="GPL-3.0-or-later" src="https://img.shields.io/badge/license-GPL--3.0--or--later-303030">
</p>

<p align="center">
  Marvelous-style cloth inside Blender: draw patterns, sew them, drape them live
  in the viewport and bake with real contact, powered by Cloth NeXt's own
  simulation engines.
</p>

<p align="center">
  <strong>Stay in Blender. No subscription. No round trip.</strong>
</p>

---

> [!NOTE]
> **LLM Transparency:** Cloth NeXt is developed with extensive assistance from
> LLM-based coding and writing tools under human direction and review.
> Project direction, architecture, UX decisions, testing requirements, release
> decisions, and final responsibility remain with the maintainer.
>
> Details are documented in
> [LLM Transparency](docs/LLM_TRANSPARENCY.md).

## What is Cloth NeXt?

Cloth NeXt is a cloth simulation add-on for Blender. Pattern making, sewing,
live draping, scene setup, validation, materials, baking, diagnostics, and
cache playback all stay inside Blender.

The simulation runs in Cloth NeXt's own engines, which the add-on installs,
updates and starts for you:

| Engine | What it does | Access |
|---|---|---|
| **AETHER Engine** | Native GPU cloth engine for the Live Simulation in the viewport (grab, pins, seams) and for bakes. Quality presets **Normal**, **Animation** and **Fitting**. Cloth only. | Included, no account needed |
| **GAIA** | Precision baking engine with contact-safe layered cloth, Rod / Cable and Soft Body roles, and cloth coupled to Blender FLIP water (Water Flow, Wet Fabric). | PolyWorks account with GAIA access |
| **TETHYS** | Wetness engine: wet areas, drying edges and droplets as textures on the cloth, no extra geometry. | Works together with GAIA |

The Physics Properties panel is the artist-facing entry point. It provides
role-aware controls, scene validation, Bake/Rebake/Cancel actions, cache state,
and actionable errors. The dedicated Bake window follows preparation and
simulation with progress, ETA, solver activity, and frame-performance history.

### What it provides

- Live Simulation in the Blender viewport with Marvelous-style grab, pins and seams
- **PATTERN NeXt**: a 2D pattern editor next to the 3D view in its own workspace
- Sewing and Object Attachments with an on-viewport toolbar
- Cloth, Rod / Cable, and volumetric Soft Body roles in shared scenes (Rod and Soft Body with GAIA)
- Static and animated Colliders, including evaluated transforms and
  topology-preserving deformation
- Blender Empty-based Gravity, Wind, Air Density, Air Friction, and Vertex Air
  Damping forces with keyframe support (engine support differs; Scene Health tells you)
- Real material parameters with understandable artist terminology
  (Surface Weight, Stretch Resistance, Bend Resistance, Friction, …)
- A categorized library of fabric presets, including research-backed
  starting points derived from the MIT Fabric Properties Dataset
- [Material Zones](docs/MATERIAL_ZONES.md): hard face selections with exact
  triangle material overrides and a visible-center Circle Select tool
- Cloth coupled to FLIP water and wetness textures (GAIA + TETHYS)
- Scene Health checks, persistent CNX error codes, support reports, and
  authenticated cache recovery
- CPU, RAM, and VRAM monitoring plus frame-performance history during Bake
- Transactional playback caches with versioned metadata, deterministic
  invalidation, and SHA-256 integrity checks
- Stable, Beta and Dev update channels through Blender's extension system

### Vertex-group Pinning

On a Cloth object, enable **Pinning** and choose one ordinary Blender vertex
group. Vertices with weight greater than `0.000001` are hard-held. **Static**
keeps their evaluated Bake Start positions; **Follow Animation** captures their
evaluated Blender positions across the Bake range on the main thread. Rebake
temporarily excludes only Cloth NeXt-owned Mesh Cache playback and preserves
the previous result until startup is ready. Timed release and soft Pull are not
exposed.

> [!NOTE]
> Material, Damping, Collision, Pressure, Pinning, Force, and quality controls
> are encoded into the selected engine's solve. Stiffness values are
> density-normalized engine values, not textbook Young's moduli in pascals.

---

## What do I need?

- Blender 5.0 or newer
- Windows x64 with a supported NVIDIA GPU (the production platform for the AETHER Engine)
- The Cloth NeXt extension
- For GAIA and TETHYS: a PolyWorks account with GAIA access

---

## Installation

### 1. Install Cloth NeXt

Cloth NeXt is installed through Blender's extension system.

Three update channels are available:

| Channel | Recommended for |
|---|---|
| **Stable** | Regular use and tested releases |
| **Beta** | Early access to newer features and fixes |
| **Dev** | Unsupported public experimental snapshots for active testing |

> [!TIP]
> Use the **Stable** channel unless you specifically want to test upcoming
> features.

Repository setup and update instructions are available in
[Update Channels](docs/UPDATE_CHANNELS.md).

### 2. Set up the engines

After installing the extension:

1. Open Blender's preferences.
2. Open the Cloth NeXt extension settings.
3. Install or update the engines there; Cloth NeXt verifies each build
   before it is used.
4. For GAIA, sign in with your PolyWorks account.

Engines are only installed and updated from inside the add-on, never by a
manual download. Cloth NeXt never switches the selected engine silently, and
AETHER and GAIA caches are kept apart.

For troubleshooting, see [Solver Installation](docs/SOLVER_INSTALLATION.md).

---

## Updates

Cloth NeXt updates install through Blender's own extension system — never
through a custom self-updater — from three official repositories
(see [Update Channels](docs/UPDATE_CHANNELS.md)):

- **Stable** — normal releases only.
- **Beta** — Beta builds plus every newer Stable release.
- **Dev** — Dev and Beta builds plus every newer Stable release; available only after enabling
  Developer Tools and explicitly acknowledging the risk.

In the add-on preferences, the *Cloth NeXt* section shows the installed
version, lets you pick the update channel, and offers *Check for Updates*.
*Add Channel Repository* registers the selected channel in Blender's
Get Extensions repositories (only on explicit click, never automatically).
*Update through Blender* synchronizes the selected repository and opens
Blender's native extension update view; the installation itself is completed
there by clicking Blender's own **Update** button — Cloth NeXt never replaces
its own files while running. Restart Blender when Blender prompts for it
after the native update. Engine updates are installed from the Cloth NeXt
preferences and are independent of add-on updates.

---

## Quick start

The current production slice supports multiple Cloth, Rod, and Soft Body
objects in one interacting solve with one or more Colliders:

1. Add Cloth NeXt physics to every object that should participate as Cloth,
   Rod, or Soft Body. All enabled deformables must use the same Bake range and
   scene-wide Contact setting.
2. Add Cloth NeXt physics to each collision mesh and set its role to
   **Collider**. Leave **Collider Motion** at **Static** for a fixed obstacle,
   or choose **Animated** to capture evaluated Blender object animation and
   topology-preserving deformation over the Bake range.
3. Optionally add Empty objects, enable Cloth NeXt, and choose **Force**.
   Gravity points along local `-Z`; Wind points along local `+Z`. Rotate the
   Empty to aim it. Multiple forces of the same type are added together.
4. Configure material, damping, collision, pressure, quality, and optional
   vertex-group pinning in Physics Properties.
5. Choose the same Bake Start and End frames for every deformable.
6. Verify that the selected engine is ready, then click **Bake**.
7. Review the resulting constant-topology Mesh Cache animation; use **Rebake**
   after changing settings or **Cancel** while a bake is active.

Animated Colliders support evaluated transforms (including parenting,
constraints and drivers) and stable-topology deformation such as Armatures,
Shape Keys and deforming modifiers. A topology change aborts before the solver
starts. Blender remains the source of Collider animation: Collider objects are
never assigned a Cloth NeXt playback cache or result modifier; only Cloth is
written back from solver output.

### Rod / Cable and Soft Body roles

Rod / Cable and Soft Body roles run with GAIA; the AETHER Engine simulates
Cloth only. Rods preserve the original Curve and write solver motion to its
control points; supported splines are Bezier and Poly. Soft Bodies use a closed
manifold mesh that is tetrahedralized automatically before simulation. Both roles participate in
the normal multi-object Bake, Collider, quality, material, and cache workflow.
NURBS Rod splines and dynamic material animation are not supported.

Rod simulation uses the Curve as a one-dimensional centerline. Curve Bevel,
Taper, and per-point radius remain visual; they are not sent to the solver. For a cable
with physical radius `r`, set **Collisions > Surface Offset** to approximately
`r` in Blender world units. This gives the centerline a uniform collision skin;
variable cable thickness is not supported yet. **Collision Gap** remains a
separate contact-barrier distance and should not be used as the cable radius.

> [!NOTE]
> The exact workflow and available controls may depend on the installed
> Cloth NeXt and engine versions.

---

## Current limitations

Some limitations may depend on:

- Blender version
- Cloth NeXt version
- Engine (AETHER, GAIA, TETHYS) and engine version
- Operating system
- GPU and driver support
- Features currently exposed through the integration

Known limitations are documented in
[Limitations](docs/LIMITATIONS.md).

When reporting an issue, please include:

- Blender version
- Cloth NeXt version
- Engine and engine version
- Operating system
- GPU model
- Error message or relevant log output

---

## Documentation

Start with the hosted [Cloth NeXt documentation](https://polyworks-art.github.io/Cloth_neXT/superhive/docs/)
or use the repository references below.

<details>
<summary><strong>User documentation</strong></summary>

- [Solver installation](docs/SOLVER_INSTALLATION.md)
- [Update channels](docs/UPDATE_CHANNELS.md)
- [Limitations](docs/LIMITATIONS.md)
- [Playback Cache Format](docs/CACHE_FORMAT.md)

</details>

<details>
<summary><strong>Developer documentation</strong></summary>

- [Architecture](docs/ARCHITECTURE.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Solver protocol](docs/PPF_PROTOCOL.md)
- [Release policy](docs/RELEASE_POLICY.md)
- [Solver distribution](docs/SOLVER_DISTRIBUTION.md)
- [Solver compatibility manifest](docs/SOLVER_COMPATIBILITY_MANIFEST.md)

</details>

> [!WARNING]
> Before changing versions, packaging, releases, update channels, or solver
> metadata, read the mandatory
> [Cloth NeXt Release Policy](docs/RELEASE_POLICY.md).

---

## License

Cloth NeXt — Copyright © 2026 Tim Christmann and Cloth NeXt contributors.

Cloth NeXt is free software licensed under the
**GNU General Public License, version 3 or, at your option, any later version**
(`GPL-3.0-or-later`, matching `blender_manifest.toml`).

See [LICENSE](LICENSE) for the full license text.

Cloth NeXt is distributed **without any warranty**, including the implied
warranties of merchantability or fitness for a particular purpose.

### Engines and third-party notices

The AETHER Engine and TETHYS are proprietary PolyWorks engines shipped as
binaries with the extension. GAIA is a PolyWorks engine derived from the
[PPF Contact Solver](https://github.com/st-tech/ppf-contact-solver)
by ST Tech / ZOZO, which is licensed under the Apache License 2.0. The engines
are not covered by the Cloth NeXt GPL license. Third-party data and notices are
listed in [Third-Party Notices](docs/THIRD_PARTY_NOTICES.md).

Croissant icon by
<a href="https://thenounproject.com/browse/icons/term/croissant/"
   target="_blank"
   rel="noopener noreferrer">
  Noun Project
</a>
