# Cloth NeXt 2.2.19 Dev

Cloth NeXt 2.2.19 returns to the established PPF-only Bake workflow and adds
the remaining advanced PPF deformation, Pin, collision, and motion controls.

## PPF-only workflow

- Choose quality, start Bake, follow progress in the Bake window, and receive
  the imported cache.
- Newton, Live Preview, the solver selector, and Newton downloads are removed.
- Existing PPF cache recovery and compatible export reuse remain available.

## Deformation and Pins

- Permanent deformation is available for Cloth, Cable / Rope, and Soft Body.
- Advanced Pin Motion supports multiple Pin Groups following different animated
  targets with individual strengths.
- Soft Constraints remain a separate table with Target, transform channel, and
  Strength columns.
- Hard Pins stay excluded from Motion Overrides.

## Collision and motion controls

- Collision Timing and Advanced Contact Distance expose audited PPF controls.
- Advanced Contact Solver provides contact iteration, correction, stability,
  GPU capacity, and response-model controls behind an expert warning.
- Motion Overrides apply a world-space Move or Spin velocity at a chosen frame.

This is Dev version `2.2.19` and is eligible only for the Dev channel.
