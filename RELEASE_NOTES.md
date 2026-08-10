# Cloth NeXt 2.2.26 Dev

Cloth NeXt 2.2.26 restores the redesigned Bake Window solver-statistic icons
and makes Wind Variation behave more like distinct gusts.

## Bake Window icons

- Contacts, Newton Steps, and Linear Iterations use their dedicated artwork.
- Icons are rendered in opaque white at 16 px for clarity.
- The existing status-bar dimensions, border, spacing, and layout are
  unchanged.

## Wind gust response

- Wind remains at its configured base strength between gusts.
- Variation adds smooth, separated positive gusts instead of continuously
  raising and lowering the base flow.
- Noise Scale continues to control gust duration, with higher values producing
  slower and broader gusts.
- The change reduces persistent flutter when Wind, Variation, and Noise Scale
  are all set to high values such as `10`.

This is Dev version `2.2.26` and is eligible only for the Dev channel.
