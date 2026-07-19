# Cloth NeXt 1.2.0 Beta

Cloth NeXt 1.2.0 refines the artist-facing Blender workflow and completes the
Superhive release-preparation pass. It does not change the Bake Companion.

## Clearer simulation controls

- **Surface Grip** is now presented as **Friction** throughout the interface,
  documentation, presets, and warnings. Existing scenes remain compatible
  because the stored property and solver mapping are unchanged.
- Cloth, Rod, Soft Body, collision, and advanced solver tooltips now explain
  the visible result first while retaining useful units and exact PPF mappings.
- Rod and Soft Body panels use practical descriptions instead of terse solver
  implementation labels.
- The redundant Overview panel has been removed from Physics Properties.

## Release and update reliability

- Release documentation and workflows consistently use plain tags such as
  `1.2.0`, without a leading `v`.
- The Blender update-handoff smoke test is deterministic and no longer waits on
  a live public repository during required CI.
- A strict timeout prevents future update-smoke regressions from occupying a CI
  runner indefinitely.

## Marketplace and support

- Superhive product copy and FAQ now describe requirements, supported roles,
  limitations, solver separation, and the first-Bake workflow consistently.
- Support guidance directs users to the stable CNX error code and
  **Export Privacy-Safe Report**. The report excludes geometry, object names,
  file contents, and full filesystem paths.

Cloth NeXt 1.2.0 remains a **Beta-channel** release. Under the numeric channel
scheme, the next Stable line is `2.0.0`.
