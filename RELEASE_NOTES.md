# Cloth NeXt 2.2.27 Dev

Cloth NeXt 2.2.27 adds verified support for the latest PPF Contact Solver under
the codename Lumen and hardens the real Blender recovery workflow.

## Solver compatibility

- Velune remains the stable default with protocol 0.13 and schema 2.
- Lumen is available with protocol 0.18 and schema 2.
- Both releases can be downloaded explicitly from Solver Preferences with
  pinned official URLs, archive sizes, and SHA-256 checksums.
- Retired protocol 0.11 installations are no longer shown or selectable.

## Lumen integration

- Protocol-specific encoding omits parameters removed by protocol 0.18.
- Exact frontend identity and required integration anchors are verified before
  installation is accepted.
- Solver `crash_kind` and multiline diagnostics are preserved end to end.

## Recovery

- Recovery startup now distinguishes a healthy control server from the
  intentionally interrupted state of the saved project.
- A real Blender 5.2 test resumed a 20-frame Lumen Bake from verified frame 10,
  continued at frame 11, skipped scene upload and rebuild, and published a
  valid 20-frame PC2 cache.

The external PPF Contact Solver remains a separate explicit download and is not
included in the Cloth NeXt extension archive. Version `2.2.27` is published
only to the Dev channel.
