# Cloth NeXt 0.2.0-beta.4

This beta publishes the Phase 2.8B UI preview through the corrected,
preflight-verified release pipeline.

## New

- Role-aware Cloth and Collider panels in Physics Properties
- Custom monochrome runtime icons
- Croissant Bake icon
- Compact and expanded Viewport HUD
- Shared bake preview state
- Optional bundled Cloth NeXt Bake companion
- Authenticated localhost communication
- Compact dark DCC-style companion window
- Cloth NeXt title and taskbar icon
- Help popup containing: SideFX, please don’t sue me.

## Fixed

- Build-time Pillow dependency
- Companion build and staging order
- Separation between source tests and built-artifact tests
- Windows-only EXE assertions now run after the Windows build
- Mandatory unpublished release preflight
- Exact commit SHA and version verification before publication

## Important

The current workflow remains a UI preview. It does not yet perform PPF scene
export, real cloth simulation, frame transfer, result import, or real cache
generation.

The external PPF Contact Solver remains separate and is not bundled.
