# Cloth NeXt 2.1.0 Beta

Cloth NeXt 2.1.0 brings the current Dev line to the Beta channel for broader
testing before the next Stable release.

## New simulation workflows

- PDRD Rigid Bodies can participate in the same solve as Cloth and use the
  same artist-oriented setup and Bake-range workflow.
- Friction can be overridden per vertex group while all remaining vertices
  continue to use the object's general Friction value.
- Blender-style Sewing turns face-less connecting edges into stitch
  constraints and closes solved seams in the playback cache.
- Wind strength can oscillate with configurable randomized variation.

## Workflow and update experience

- The Physics panel shows the installed version and whether the selected
  GitHub channel offers an update.
- Repository registration is available directly below the update-channel
  selector.
- The Cloth NeXt Physics button now follows Blender's two-column grid and uses
  the white Cloth NeXt logo.

## Bake reliability and diagnostics

- Rigged deformables are exported in their Bake-start pose and playback is
  inserted after the last enabled Armature modifier.
- Multi-object Bake ranges include Cloth, Rod, Soft Body, and Rigid Body
  objects consistently.
- Cache attachment, solver startup, cancellation, and error reporting received
  additional hardening.
- Solver-reported self-intersections now show a concise error instead of a
  full internal process trace in Blender.
- Experimental shell Shrink remains disabled until the external solver offers
  a stable implementation.

## Distribution

- Release tag: `2.1.0` — no leading `v`.
- Channel: **Beta**; the verified package is published to Beta and Dev only.
- The PPF Contact Solver remains separate and is downloaded only from its
  manifest-pinned official upstream release after explicit confirmation.
