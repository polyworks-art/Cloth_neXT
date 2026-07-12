# Cloth NeXt 0.2.0-beta.6

This beta introduces Cloth NeXt's first real PPF solver vertical slice for
interactive Blender testing.

## First real simulation test

The new developer workflow can:

- snapshot one Cloth NeXt cloth object
- snapshot one static collider
- encode and upload a PPF 0.11 scene
- start the real external solver
- build and simulate eight frames
- retrieve and validate completed frames
- create constant-topology playback in Blender
- display real progress in the panel, HUD, and companion window
- cancel and clean up owned solver sessions

## Also fixed

- Blender's automatic update path no longer fails because disabled extension
  repositories shift the repository index
- Cloth NeXt now targets its exact repository and package through Blender's own
  extension system
- incomplete solver frames are rejected instead of imported
- worker, timer, subscription, and companion-cancellation cleanup was hardened

## Experimental scope

This is a Phase 3A test build. It currently supports only one cloth, one static
collider, eight frames, constant topology, and a limited verified material
configuration. The interactive Blender 5.1.2 timeline, collision, cancellation,
and cache test is the primary purpose of this beta. It is not production ready,
and interactive acceptance has not yet passed.

## External solver

The PPF Contact Solver is external software developed by ST Tech / ZOZO. It is
not bundled, mirrored, or redistributed with Cloth NeXt. A compatible separately
installed solver is required.
