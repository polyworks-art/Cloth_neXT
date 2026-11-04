# Cloth NeXt — Superhive Product Page

## Short description

Marvelous-style cloth inside Blender. Draw patterns, sew them and drape them
live in the viewport, then bake with real contact. Cloth NeXt runs on its own
simulation engines: the AETHER Engine for live draping and bakes, GAIA for
precision bakes and cloth in water, and TETHYS for wet fabric. No subscription,
no round trip to another application.

## Why Cloth NeXt

- Grab, pin and sew cloth live in the Blender viewport with the AETHER Engine
- Draw and edit patterns in PATTERN NeXt, a 2D editor next to your 3D view
- Bake layered garments on animated characters with real contact
- Couple cloth to FLIP water and add wetness textures (GAIA + TETHYS)
- Start quickly with categorized fabric presets
- Validate common scene, geometry, storage, and memory problems before a long Bake
- Recover safely from incomplete or invalid caches
- Export a privacy-safe diagnostic report when support is needed

## Requirements

- Windows x64
- Blender 5.0 or newer
- A supported NVIDIA GPU
- Enough RAM, VRAM, and disk space for the selected geometry and frame range
- Internet access for the guided engine setup
- For GAIA and TETHYS: a PolyWorks account with GAIA access

The engines are installed and updated from inside the Cloth NeXt preferences
after you confirm the download.

## Installation

1. Download the Cloth NeXt ZIP and leave it packed.
2. In Blender, open **Edit → Preferences → Extensions**.
3. Choose **Install from Disk**, select the ZIP, and enable Cloth NeXt.
4. Open the Cloth NeXt preferences and follow the guided engine setup.
5. Run **Solver Health Check**.
6. Open the included Quick Start file and run **Scene Health Check** before the
   first Bake.

## Quick Start

1. Select a Mesh or Curve and enable Cloth NeXt in Physics Properties.
2. Choose Cloth, Collider, Rod/Cable, Soft Body, or Force.
3. Select a material preset and set the Bake range.
4. For animated colliders, start with 8 Motion Samples per Frame. Fast or curved
   motion may benefit from 12–16 samples.
5. Run Scene Health Check and resolve any red items.
6. Press **Bake**. Cloth NeXt validates the scene and creates a verified playback cache.

## Included features

- Cloth, Rod/Cable, and Soft Body simulation roles
- Multiple deformables and colliders in one shared simulation
- Static and Follow Animation pins for Cloth
- Static, rigid animated, and deforming animated colliders
- Gravity, Wind, Air Density, Air Friction, and Vertex Air Damping forces
- Friction, Collision Gap, and Surface Offset controls per object
- 37 categorized fabric presets
- Solver-quality presets with optional advanced controls
- CPU, RAM, and VRAM monitoring with configurable RAM Auto Cancel
- Versioned and authenticated PC2 playback caches
- Scene Health checks, cache recovery, stable error codes, and privacy-safe reports
- Stable, Beta, and Dev update channels through Blender Extensions

## Current limitations

- Windows with a compatible NVIDIA GPU is the supported production platform.
- Rod/Cable simulation uses a uniform collision radius. Curve bevel and point
  radius remain visual and do not create variable physical cable thickness.
- Soft Body requires a closed manifold mesh. Cloth NeXt creates the internal
  tetrahedral volume automatically.
- Animated topology changes are rejected before the Bake.
- Sewing through face-less mesh edges is supported. Tearing, plasticity, timed
  pin release, and multiple pin groups are not currently exposed.
- Simulation results depend on mesh scale and resolution, material values,
  solver quality, and available hardware. Presets are reliable starting points,
  not guaranteed final settings for every scene.

## Support

If a Bake fails, keep the visible `CNX-E…` error code and choose
**Export Privacy-Safe Report** in Scene Health. Send the report together with your
Blender version, GPU model, and short reproduction steps. The report excludes mesh
geometry, object names, file contents, and full filesystem paths.

Support: use the support contact shown on the Superhive product page or open a
GitHub issue at <https://github.com/polyworks-art/Cloth_neXT/issues>.

## Licensing

The Cloth NeXt add-on is provided under GPL-3.0-or-later. The AETHER Engine,
GAIA and TETHYS are PolyWorks engines and are not covered by the GPL. GAIA is
derived from the PPF Contact Solver by ST Tech / ZOZO (Apache License 2.0).
