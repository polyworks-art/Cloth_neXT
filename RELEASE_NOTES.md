# Cloth NeXt 2.2.44

Cloth NeXt 2.2.44 makes geometry diagnostics easier to understand and repair,
while safely reusing animated collider captures between compatible Bakes.

## Clearer geometry diagnostics

- The viewport now shows every mapped intersection and degenerate face from the
  retained solver input at the same time.
- The Simulation panel presents a concise combined count in a dedicated card.
- Auto Fix and Clear are visually separated, with Auto Fix emphasized as the
  primary action while Clear remains independently available.

## Safer, more transparent Auto Fix

- One undoable Auto Fix action repairs every safely supported intersection and
  degenerate face without changing topology.
- Blender's status bar reports progress through validation, planning, vertex
  correction, and completion.
- Preflight selections no longer leave Auto Fix blocked in Edit Mode.
- A correction is skipped safely when its bounded movement would leave the
  confirmed faces intersecting; Auto Fix no longer claims that case as fixed.
- Auto Fix never starts a Bake; the artist remains in control of verification.

## Faster compatible Re-Bakes

- Evaluated animated collider samples can be reused across Bakes.
- Reuse is rejected whenever geometry, transforms, sample timing, or the
  solver-space export contract changes.

## Verification

- Full Python suite: 1,431 passed, 9 skipped, 3 deselected.
- Blender 5.2.0 LTS built and validated the Windows extension locally; all 3
  packaged-artifact tests and the forbidden-solver-material scan passed.
- Automated coverage includes cache reuse and invalidation, combined diagnostic
  rendering, multi-issue repair, safe intersection rejection, and progress
  lifecycle handling.
- Blender/Lumen validation used the updated real Top-and-Shorts scene and
  revalidated the isolated Shorts diagnostic after Auto Fix.
- The external PPF Contact Solver is not bundled or modified.
