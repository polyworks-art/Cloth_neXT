# Cloth NeXt 2.2.31

Cloth NeXt 2.2.31 restores periodic automatic Recovery checkpoints for the
Lumen solver.

## Recovery

- The configured Auto Save Checkpoints interval is passed to the solver and
  is now reflected immediately in Cloth NeXt's durable Recovery metadata.
- Lumen's normal running status may omit the legacy `saved_states` field.
  Cloth NeXt now verifies its atomically completed `state_<frame>.bin.gz`
  files during ordinary status polling, using the same authoritative fallback
  already proven by Save on Cancel.
- Automatic checkpoints therefore appear while the Bake continues; cancelling
  is no longer required to make them visible or resumable.
- Retention continues to follow the configured Keep Saved States value.

## Validation

- Regression coverage reproduces a Lumen status response without
  `saved_states` while a periodic state exists on disk.
- Full Python suite: 1,358 passed, 9 skipped, 3 deselected.
- The external PPF Contact Solver is not bundled.
