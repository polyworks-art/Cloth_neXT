# Cloth NeXt update repositories

From 2.7.0, normal customers receive updates through Superhive's native Blender
Extension Repository. The Update Channel selector and Dev acknowledgement are
removed. Cloth NeXt synchronizes its owning repository through Blender, reads
that repository's cached index and hands installation to Blender. It preserves
the repository URL and identity, accepts every newer version and does not offer
channel switches or automatic downgrades. No custom authentication is added.

Private GitHub publication is separate from customer distribution. Every version
greater than 2.6.0, including internal Dev versions, is published to one common
repository. Its URL is stored only in the GitHub Actions secret
`CLOTH_NEXT_RELEASE_REPOSITORY_URL`; it must never appear in tracked files or the
add-on package. The publish and repair workflows resolve the destination from
that secret and mask the derived directory in logs. Missing configuration fails
without changing legacy feeds.

The historical Stable, Beta and Dev repositories remain available with their
existing contents. Beta remains permanently frozen at 2.6.0 as a migration bridge.
The 2.6.0 artifact has no QuickAdd and continues to work. Quick Assign starts in
2.7.0. Versions after 2.6.0 are not added to any historical channel, including Dev.
This supersedes the earlier separate internal Dev publication rule.

Use the official guide to connect Superhive in Blender:
https://support.superhivemarket.com/article/335-connecting-superhive-as-a-remote-repository-in-blender

Installing from disk does not embed or auto-configure any private URL. Configure
the intended native repository in Blender to receive updates.

Preferences show a Superhive status section instead of update controls:
`Superhive connected` means an enabled official Superhive repository is configured;
`Purchase validated` means that repository's locally cached Blender index contains
`cloth_next`. These are informational states only. They read no access tokens,
perform no network request in drawing, and never enable or disable features.
