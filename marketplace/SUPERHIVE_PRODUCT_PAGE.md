# Cloth NeXt — Superhive Product Page Draft

## Short description

Cloth NeXt brings the PPF Contact Solver into a focused Blender workflow for
multi-object cloth, cable/rod and experimental soft-body simulation. It adds
material presets, animated colliders, forces, pins, monitored baking and
authenticated playback caches while keeping the external solver installation
separate and explicit.

## Requirements

- Windows x64
- Blender 5.0 or newer (test every version selected on the product page)
- NVIDIA GPU and a driver/runtime compatible with the supported official PPF release
- The external PPF Contact Solver, installed separately after confirmation
- Sufficient system RAM, VRAM and cache-disk space for the chosen mesh and frame range

The marketplace download does not contain or redistribute the PPF solver.

## Installation

1. Download the Cloth NeXt extension ZIP. Do not unpack it.
2. In Blender, open **Edit → Preferences → Extensions**.
3. Use **Install from Disk**, select the ZIP and enable Cloth NeXt.
4. Open the Cloth NeXt preferences and select or install the supported official
   PPF solver after reviewing the confirmation dialog.
5. Run **Solver Health Check**.
6. Open the supplied Quick Start `.blend`, select `Demo_Cloth`, and run
   **Scene Health Check** before the first Bake.

## Quick Start

1. Select a Mesh and enable Cloth NeXt in Physics Properties.
2. Choose Cloth, Collider, Rod/Cable, Soft Body, or Force as appropriate.
3. Assign a material preset and set Bake Start/End.
4. For animated colliders, use at least 8 motion samples per frame; use 12–16
   for fast or strongly curved motion.
5. Run Scene Health Check and resolve red items.
6. Press Bake. The result is attached as a verified playback cache.

## Included features

- Multiple deformables and colliders in one solve
- Cloth, Rod/Cable and experimental Soft Body roles
- Static and Follow Animation pins for Cloth
- Animated rigid or deforming colliders
- Gravity, Wind and supported PPF aerodynamic forces
- CPU, RAM and VRAM monitoring with RAM auto-cancel
- Versioned, authenticated PC2 playback caches
- Scene Health preflight, cache recovery and privacy-safe support reports
- Stable, Beta and Dev update channels through Blender Extensions

## Important limitations

- Windows/NVIDIA is the currently supported production platform.
- Rod thickness uses a uniform collision-radius approximation; variable physical
  cable thickness is not supported.
- Soft Body requires a closed manifold mesh.
- Animated topology changes are rejected.
- Stitching, tearing, plasticity, timed pin release and multiple pin groups are
  not currently exposed.
- Results depend on mesh scale, resolution, material values, time step and available
  hardware. Presets are starting points rather than guarantees.

## Support

Run **Export Privacy-Safe Report** before opening a support request. Include the
generated report, the visible `CNX-E…` code, Blender version, GPU model and concise
reproduction steps. The report contains no mesh geometry or file contents.

## Licensing

Cloth NeXt is GPL-3.0-or-later. PPF is a separate third-party project and is not
included in the Cloth NeXt download.
