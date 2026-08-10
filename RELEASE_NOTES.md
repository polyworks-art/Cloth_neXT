# Cloth NeXt 2.2.25 Dev

Cloth NeXt 2.2.25 adds direct control over Wind Variation speed.

## Wind Noise Scale

- Noise Scale appears directly below Wind Variation.
- Its value controls the gust time scale in seconds.
- Higher values produce slower, broader wind changes.
- Lower values produce faster wind detail.
- The default `3.0 s` slows the multi-scale noise introduced in 2.2.24.
- The tooltip explains the timing behavior, and Noise Scale is included in
  cache fingerprints so changes correctly require a Rebake.

This is Dev version `2.2.25` and is eligible only for the Dev channel.
