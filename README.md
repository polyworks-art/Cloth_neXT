<p align="center">
  <img
    src="assets/Cloth_neXt_icon.svg"
    alt="Cloth NeXt Logo"
    width="240"
  />
</p>

<h1 align="center">Cloth NeXt</h1>

<p align="center">
  Cloth NeXt acts as a Blender bridge for the separately installed
  <strong>PPF Contact Solver</strong>, which is developed and distributed by ST Tech / ZOZO.
</p>


<p align="center">
  <strong>Stay in Blender. Simulate externally. Keep control of the workflow.</strong>
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

Cloth NeXt brings the external PPF Contact Solver into a workflow that feels familiar to artists who already enjoy Blender’s built-in cloth system.

It is designed for users who want powerful GPU-based cloth simulation while keeping a clear, Blender-focused setup with familiar object roles, accessible controls, and sensible defaults.

The Physics Properties **Solver** panel is the normal artist-facing entry
point: it reports solver readiness and provides Bake/Rebake/Cancel controls,
material/cache summary, and synchronized progress. When enabled in Add-on
Preferences, the reusable Bake companion opens automatically; Blender's HUD
and Physics UI remain functional when that optional window is disabled or
cannot be opened.

Cloth NeXt also provides a more guided and streamlined alternative for artists who find the existing PPF Blender integration too technical or difficult to navigate, while the solver itself remains a separate, independently installed application.

### What it provides

- Blender-centered cloth simulation workflow
- Integration with the GPU-based PPF Contact Solver
- Guided solver installation and selection
- Solver version and compatibility checks
- Stable and beta update channels
- Open-source Blender extension
- Clear separation between the add-on and external solver
- A native, role-aware Physics Properties configuration workflow
- Experimental Rod / Cable simulation for Bezier and Poly Curves, plus
  volumetric Soft Body simulation for closed manifold meshes
- Real material parameters with understandable artist terminology
  (Surface Weight, Stretch Resistance, Bend Resistance, Surface Grip, …),
  each mapped one-to-one to a documented PPF solver parameter
- Official PPF-derived fabric presets (Silk, Flag, Cotton, Wool, Denim,
  Leather) bundled as read-only data, with exact provenance and the
  Apache-2.0 upstream notice preserved
- An optional display-only bake status HUD
- Transactional playback caches with versioned metadata, deterministic
  invalidation, and SHA-256 integrity checks

### Vertex-group Pinning

On a Cloth object, enable **Pinning** and choose one ordinary Blender vertex
group. Vertices with weight greater than `0.000001` are hard-held. **Static**
keeps their evaluated Bake Start positions; **Follow Animation** captures their
evaluated Blender positions across the Bake range on the main thread. Rebake
temporarily excludes only Cloth NeXt-owned Mesh Cache playback and preserves
the previous result until startup is ready. Timed release and soft Pull are not
exposed.

> [!NOTE]
> Phase 3B connects the Material, Damping, and Collision properties to the
> real PPF solver: every visible, editable value is actually encoded and
> sent. The current scope is one cloth and one or more colliders, with an
> artist-selected Bake range. Developer Test Tools retain diagnostics and
> test-scene creation. Note that PPF's stiffness is a
> density-normalized value, not a textbook Young's modulus in pascals (see
> [PPF parameter mapping](docs/PPF_PARAMETER_MAPPING.md)). PPF itself
> remains separate, independently installed software.

> [!IMPORTANT]
> The PPF Contact Solver is external software developed and distributed by
> ST Tech / ZOZO (`st-tech/ppf-contact-solver`).
>
> Cloth NeXt never bundles, mirrors, or redistributes the solver. It is installed
> or selected separately, after explicit confirmation, directly from its
> official source.

---

## What do I need?

To use Cloth NeXt, you need:

- A compatible version of Blender
- The Cloth NeXt extension
- A separately installed PPF Contact Solver
- Compatible hardware and operating system support for the selected solver version

> [!NOTE]
> Cloth NeXt is the Blender integration layer. The actual simulation is performed
> by the external PPF Contact Solver.

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

### 2. Install or select the solver

After installing the extension:

1. Open Blender's preferences.
2. Open the Cloth NeXt extension settings.
3. Choose the solver installation or selection option.
4. Confirm access to the external official source.
5. Install the solver or select an existing installation.
6. Let Cloth NeXt verify compatibility.

For manual installation and troubleshooting, see
[Solver Installation](docs/SOLVER_INSTALLATION.md).

---

## Updates

Cloth NeXt updates install through Blender's own extension system — never
through a custom self-updater — from three official repositories
(see [Update Channels](docs/UPDATE_CHANNELS.md)):

- **Stable** — normal releases only.
- **Beta** — beta and release-candidate prereleases.
- **Dev** — unsupported experimental snapshots, available only after enabling
  Developer Tools and explicitly acknowledging the risk.

In the add-on preferences, the *Cloth NeXt* section shows the installed
version, lets you pick the update channel, and offers *Check for Updates*.
*Add Channel Repository* registers the selected channel in Blender's
Get Extensions repositories (only on explicit click, never automatically).
*Update through Blender* synchronizes the selected repository and opens
Blender's native extension update view; the installation itself is completed
there by clicking Blender's own **Update** button — Cloth NeXt never replaces
its own files while running. Restart Blender when Blender prompts for it
after the native update. Add-on updates never touch the separately installed
PPF solver, and solver updates never touch the add-on.

---

## Quick start

The current production slice supports one Cloth object and one or more Colliders:

1. Add Cloth NeXt physics to the mesh that should behave as cloth.
2. Add Cloth NeXt physics to each collision mesh and set its role to
   **Collider**. Leave **Collider Motion** at **Static** for a fixed obstacle,
   or choose **Animated** to capture evaluated Blender object animation and
   topology-preserving deformation over the Bake range.
3. Configure material, damping, collision, pressure, quality, and optional
   vertex-group pinning in Physics Properties.
4. Choose the Bake Start and End frames in the Cloth NeXt Solver panel.
5. Verify that the external solver is ready, then click **Bake**.
6. Review the resulting constant-topology Mesh Cache animation; use **Rebake**
   after changing settings or **Cancel** while a bake is active.

Animated Colliders support evaluated transforms (including parenting,
constraints and drivers) and stable-topology deformation such as Armatures,
Shape Keys and deforming modifiers. A topology change aborts before the solver
starts. Blender remains the source of Collider animation: Collider objects are
never assigned a Cloth NeXt playback cache or result modifier; only Cloth is
written back from solver output.

### Experimental Rod and Soft Body roles

The Dev workflow also accepts one **Rod / Cable** or **Soft Body** instead of
the Cloth object. Rods preserve the original Curve and write solver motion to
its control points; supported splines are Bezier and Poly. Soft Bodies use a
closed manifold mesh and request PPF tetrahedralization before simulation.
Both roles use the normal Bake range, Collider, quality, material, and cache
workflow. Rod/Soft Body pinning, NURBS Rod splines, dynamic material animation,
and multiple deformable objects are not supported yet.

> [!NOTE]
> The exact workflow and available controls may depend on the installed
> Cloth NeXt and solver versions.

---

## Why use an external solver?

Cloth NeXt and the PPF Contact Solver are separate projects by design.

This keeps the workflow transparent:

- The solver remains connected to its official source.
- Third-party binaries are not redistributed.
- Users explicitly choose which solver installation is used.
- Solver updates remain independent from add-on updates.
- Licensing and ownership stay clearly separated.
- Compatibility can be validated before a simulation is started.

---

## Current limitations

Cloth NeXt is an integration layer around an external simulation system.
Some limitations may depend on:

- Blender version
- Cloth NeXt version
- PPF Contact Solver version
- Operating system
- GPU and driver support
- Features currently exposed through the integration

Known limitations are documented in
[Limitations](docs/LIMITATIONS.md).

When reporting an issue, please include:

- Blender version
- Cloth NeXt version
- Solver version
- Operating system
- GPU model
- Error message or relevant log output

---

## Documentation

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
- [PPF protocol](docs/PPF_PROTOCOL.md)
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

### External solver

The PPF Contact Solver is separate software developed and distributed by
ST Tech / ZOZO under the Apache License 2.0.

It is not covered by the Cloth NeXt license and is neither included in nor
distributed with Cloth NeXt.

Croissant icon by
<a href="https://thenounproject.com/browse/icons/term/croissant/"
   target="_blank"
   rel="noopener noreferrer">
  Noun Project
</a>
