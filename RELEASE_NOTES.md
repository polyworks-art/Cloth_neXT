# Cloth NeXt 2.2.23 Dev

Cloth NeXt 2.2.23 repairs the live deformation feedback introduced in 2.2.22.

## Live Bake cache playback

- Completed PC2 frames are flushed to the private growing cache before the
  Blender main thread receives the progress event.
- Blender's Mesh Cache modifier is pointed at that growing file during Bake,
  rather than at the final path that does not exist until publication.
- The timeline advances only after the matching deformation frame is readable.
- Single- and multi-object bakes use the same live-path handoff while final
  cache validation and atomic publication remain unchanged.

This is Dev version `2.2.23` and is eligible only for the Dev channel.
