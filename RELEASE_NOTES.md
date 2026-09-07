# Cloth NeXt 2.4.2 Dev

Cloth NeXt 2.4.2 prevents stale Recovery partials from breaking fresh Bakes.

## Bake reliability

- Fresh Bakes never append to a partial from an earlier run.
- Explicit Resume still continues from the matching authenticated partial.
- The intermittent `more frames than declared were written` cache failure is
  prevented before the solver worker starts writing the new cache.

## Viewport shading

- Random, Material, and other color modes no longer reset to Object.
- The recurring timer that forced Object color mode has been removed.
- Preferences > Viewport Colors now includes Show Cloth NeXt Role Colors,
  disabled by default. Enabling it never changes your shading selection.
- A visible hint explains that role colors require Solid shading > Color > Object.
- Disabling role colors restores original object colors, including saved role
  colors when opening a file with this preference disabled.

## Release scope

2.4.2 is a Dev-channel release. The external PPF Contact Solver is unchanged,
separately installed, and never bundled. Beta and Stable remain unchanged.
