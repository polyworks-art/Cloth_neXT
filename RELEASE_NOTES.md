# Cloth NeXt 2.2.30

Cloth NeXt 2.2.30 makes live Bake framing calm and continuous and fixes
Recovery for complex scenes whose evaluated state cannot use the optional
persistent export cache.

## Live Bake viewport

- Auto-Framer motion is driven by a refresh-rate-independent timer rather than
  by the irregular completion cadence of solver frames.
- Smooth and Cinematic modes interpolate continuously between new targets.
- A small framing dead zone prevents evaluated bounding-box noise from making
  the viewport hunt back and forth.
- Pull-back motion is eased instead of snapping outward multiple times.

## Recovery

- Recovery no longer depends on eligibility for the optional persistent Scene
  export cache.
- When that cache is unsafe for an evaluated scene, the canonical Scene hash
  sent to the solver supplies a stable durable Recovery identity.
- Auto-save interval, retention, and Save on Cancel therefore remain attached
  to the production Bake instead of leaving solver states only in Blender's
  temporary run directory.

## Validation

- Auto-Framer timing, Cinematic response, and jitter dead-zone unit coverage.
- Regression coverage for durable Recovery without a Scene export-cache key.
- Full Python suite and release-policy/package gates are required before
  publication. The external PPF Contact Solver is not bundled.
