# Cloth NeXt 2.2.24 Dev

Cloth NeXt 2.2.24 makes animated Wind Variation feel less uniform.

## Natural gust timing

- Wind Variation now uses deterministic multi-scale smooth noise.
- Slow atmospheric pressure changes are layered with shorter gusts instead of
  two continuously repeating sine waves.
- Every Force object retains a stable, reproducible pattern.
- The resulting strength remains strictly bounded by the configured Wind
  Variation value.

The current PPF interface exposes one scene-wide wind vector, so this release
improves temporal variation; spatial turbulence across a cloth surface remains
outside the solver contract.

This is Dev version `2.2.24` and is eligible only for the Dev channel.
