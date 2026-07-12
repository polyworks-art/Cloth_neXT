# Cloth NeXt 0.2.0-beta.3

This beta introduces the completed Phase 2.8B user-interface shell for
interactive Blender testing. Final visual QA is intentionally still open.

## New

- Native role-aware Cloth NeXt panels in Physics Properties
- Separate Cloth and Collider interfaces
- Custom monochrome icon system
- Croissant Bake icon
- Compact and expanded Viewport HUD
- Shared bake status model
- Explicit UI preview workflow
- Optional Cloth NeXt Bake companion source application
- Authenticated localhost communication between Blender and the companion
- Reload-safe UI, HUD, icon, timer, IPC, and companion cleanup

## Important

The current Bake workflow is a UI preview only. This release does not yet
perform PPF scene export, real cloth simulation, frame transfer, result import,
or real cache generation. The real PPF simulation pipeline follows in Phase 3.

## Testing requested

Please test Cloth and Collider panel layout, custom icon appearance, croissant
icon readability, HUD positioning and contrast, narrow and large viewports,
Blender UI scaling, Windows DPI scaling, companion preview behavior, add-on
disable/re-enable lifecycle, role switching, preview cancellation, and errors.

## Companion application

The Cloth NeXt-owned Bake companion is built from tagged source by release CI,
bundled at one validated location inside the Windows extension, and never
published as a separate release asset.

## External solver

The PPF Contact Solver is external software by ST Tech / ZOZO. It is not
bundled, mirrored, or redistributed with Cloth NeXt.
