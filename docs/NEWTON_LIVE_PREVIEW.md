# Newton solver backend

Newton Physics is an optional offline Bake backend for Cloth NeXt. Live Preview
is intentionally not part of the product workflow. Artists select **PPF** or
**Newton**, choose a Cloth NeXt quality level, and use the same Bake action,
Bake window, progress controller, PC2 publication, and cache attachment flow.

## Verified runtime boundary

- Newton Physics 1.4.0
- Warp 1.15.0
- pytetwild/fTetWild 0.3.0
- external CPython 3.11 environment
- owned bounded JSON worker protocol

Newton, Warp, and pytetwild are never installed into Blender Python or bundled
with the extension. The managed environment lives under LocalAppData and is
installed only after an explicit user action. Add-on import and registration do
not import Newton, start a process, or require either external solver.

## Current verified Cloth NeXt scope

Newton supports offline Bake for mixed Cloth, Soft Body, and Rigid Body scenes,
plus static and animated/deforming triangle Colliders with stable topology,
gravity, static pins, Follow Animation pins, self-contact, PC2 cache publication,
cancellation, and the normal Bake window lifecycle. Soft Body surfaces are
tetrahedralized by fTetWild in the isolated worker and mapped back to the
original Blender surface for cache playback.

Pressure, Sewing, non-gravity Force objects, Rod, Soft Body pinning/rest-volume
scaling, persistent Recovery checkpoints, and topology-changing animation remain unavailable in
the Cloth NeXt Newton backend. These are rejected before worker startup rather
than silently ignored.

Soft Bodies require a closed input surface and the fTetWild selection. Invalid
or degenerate volumes fail before simulation; Cloth NeXt never creates a fake
centroid-fan volume.

## Quality

The artist-facing Low, Medium, High, and Extreme levels are backend-neutral.
PPF resolves them to its native time-step/Newton/PCG settings. Newton resolves
them independently to VBD substeps and iterations. PPF raw controls are not
reinterpreted as Newton controls. Newton-specific custom substeps and iterations
are available only in the selected backend's Advanced section.

## Materials

Cloth NeXt retains one canonical material in the `.blend`. PPF keeps its exact
verified mapping. Newton applies an explicitly approximate VBD mapping for
weight, stretch, sideways response, bend, damping, friction, gap, and offset.
Stretch Limit is retained but unsupported by Newton. Switching backends never
overwrites canonical values.

Soft Body density, stiffness, Poisson ratio, damping, friction and collision
spacing map to Newton's tetrahedral FEM/VBD parameters. Rigid Body density,
friction and collision spacing map to Newton body/shape parameters. These are
backend translations, not numerical parity claims with PPF.

## Cache and recovery

Cache provenance records the backend and Newton/Worker protocol versions. The
attached result stores its backend identity, so changing PPF to Newton or back
marks the current result stale. PPF Recovery remains PPF-only; Newton never
attempts to consume or emulate PPF recovery state.
