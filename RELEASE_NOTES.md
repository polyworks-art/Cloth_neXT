# Cloth NeXt 2.9.21

Wind on Cloth NeXt Force Emptys now always uses a local field. The global/local toggle is removed, including the effect of saved old toggle values. Existing positions, directions and strength values are retained; Wind fades to zero at Radius, which defaults to 2 m. Adjust Radius to cover the intended area when updating a scene that previously used global Wind. Fields require GAIA 0.23 and positive Air Density.

The revised controls group field shape, gusts and turbulence, obstacle response, preview quality, and aerodynamic response. Speed, Radius and Falloff stay directly visible. Wind animated from zero is captured, while static gravity-only sources skip Wind export.

Bake preparation now reports Wind animation capture, Sampling local Wind, and field encoding with completed work counts. Geometry work without a known total stays indeterminate instead of displaying a fixed zero percent. Cancel preserves the original frame and subframe and does not report unfinished Wind work as complete.

Validation includes 2,175 passing source tests, real Blender export and cancellation regressions, animated Wind from zero, ignored legacy toggles, multi-object grid export, cache status checks, and a visual inspection of the actual controls. The external solver is not modified or bundled.

Update through Blender's native extension manager and restart Blender. The external solver is not modified or bundled.
