# Cloth NeXt 2.2.37

Cloth NeXt 2.2.37 refines the new logo assets by removing unused transparent
canvas while keeping every visible pixel and color unchanged.

## Tighter logo assets

- Both the color and monochrome source logos are cropped exactly to their
  visible alpha bounds.
- The canonical add-on logo is refreshed from the cropped color source.

## Companion identity

- Companion PNG and Windows ICO assets are regenerated deterministically.
- The application-icon pipeline preserves the logo's original proportions when
  fitting it into the square icon canvas.

## Validation

- Asset tests verify exact source dimensions, edge-tight alpha bounds,
  pixel-preserving source crops, and repeatable PNG/ICO output.
- The external PPF Contact Solver is not bundled.
