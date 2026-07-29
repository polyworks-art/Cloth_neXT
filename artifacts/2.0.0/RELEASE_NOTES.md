# Cloth NeXt 2.0.0 Stable

Cloth NeXt 2.0.0 is the Stable promotion of the successfully published 1.2.0
Beta line. It brings the complete current Cloth NeXt workflow to the Stable,
Beta, and Dev update channels through one verified release artifact.

## Simulation workflow

- Cloth, Rod, Soft Body, static Collider, and animated Collider roles are
  available in the same multi-object solve.
- Animated Collider sampling and optional generated low-poly proxies support
  moving and deforming collision geometry while keeping memory requirements
  visible before Bake.
- Multiple Gravity, Wind, Air Density, Air Friction, and Vertex Air Damping
  Forces can be combined and animated.
- Thirty-seven categorized fabric presets provide practical starting points;
  thirty are derived from the MIT Fabric Properties Dataset with documented
  provenance and conversion.

## Artist-facing controls

- Collision controls use the direct terms **Friction**, **Collision Gap**, and
  **Surface Offset**.
- Cloth, Rod, Soft Body, collision, quality, and solver controls describe the
  visible effect first while retaining exact PPF names and units where useful.
- The redundant Overview panel has been removed from Physics Properties.

## Bake reliability and diagnostics

- The continuous Bake Companion covers preparation and simulation without
  opening a replacement window. Details contain a full-width per-frame
  performance graph with the ETA centered below it.
- Error states replace the graph with the stable CNX code, recovery guidance,
  and a direct documentation link while keeping the bottom controls visible.
- Recovery actions can update from the public Cloth NeXt error directory
  without sending scene data, filenames, or diagnostics. Bundled offline
  guidance remains available when the network is unavailable.
- Scene Health, authenticated cache recovery, transactional multi-object cache
  publication, local diagnostics, and privacy-safe support reports make failed
  or interrupted Bakes easier to inspect and recover.

## Distribution

- Release tag: `2.0.0` — no leading `v`.
- Channel: **Stable**.
- The PPF Contact Solver remains separate and is downloaded only from its
  manifest-pinned official upstream release after explicit confirmation.
- The release workflow publishes the same SHA-256-verified extension ZIP to
  the Stable, Beta, and Dev Blender repositories.
