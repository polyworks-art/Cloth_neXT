# Cloth NeXt 0.4.0 Beta

This first numeric-channel Beta brings the production Bake workflow together
for broader testing in Blender 5.x.

## Simulation workflow

- Bake multiple Cloth, Rod, and Soft Body objects in one shared PPF project.
- Use Follow Animation pins in multi-object bakes.
- Add every force supported by the current PPF mapping to Empties and animate
  those properties with Blender keyframes.
- Bake without creating a Collider. Cloth NeXt supplies a tiny remote internal
  STATIC sentinel because PPF 0.11 currently requires a STATIC group to build;
  it never appears in the Blender scene or cache metadata.

## Safety and interface

- Automatically cancel a Bake when total system RAM remains above a
  configurable threshold for two telemetry samples. The default is 90%.
- Show the RAM limit as a red line in the redesigned CPU/RAM/VRAM resource HUD.
- Use smoothly drifting and rotating Cloth NeXt icons in the Bake companion.
- Use role-specific icons throughout the physics dropdown, including Forces.

## Compatibility

The PPF Contact Solver remains an external dependency and is not included in
the add-on. This Beta uses the `STABLE.BETA.DEV` numbering scheme: `0.4.0` is
the Beta build and `0.4.1` is its matching Dev snapshot.

Back up production scenes and caches before Beta testing.
