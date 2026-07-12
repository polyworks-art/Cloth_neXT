# Cloth NeXt 0.2.0-beta.5

This beta ships the intended compact companion window that was missing from
0.2.0-beta.4, whose bundled companion was built from an older UI state.

## Fixed

- Bundled Cloth NeXt Bake companion now uses the compact 370x108 dark
  DCC-style window intended for 0.2.0-beta.4
- Companion progress bar and its label now resize with the window
- Companion bottom row uses a responsive layout instead of fixed pixel
  positions

## Unchanged from 0.2.0-beta.4

- Role-aware Cloth and Collider panels in Physics Properties
- Custom monochrome runtime icons, croissant Bake icon
- Compact and expanded Viewport HUD, shared bake preview state
- Optional bundled Cloth NeXt Bake companion with authenticated localhost
  communication, Cloth NeXt title and taskbar icon
- Help popup containing: SideFX, please don’t sue me.
- Preflight-verified release pipeline with exact commit SHA and version
  verification

## Important

The current workflow remains a UI preview. It does not yet perform PPF scene
export, real cloth simulation, frame transfer, result import, or real cache
generation.

The external PPF Contact Solver remains separate and is not bundled.
