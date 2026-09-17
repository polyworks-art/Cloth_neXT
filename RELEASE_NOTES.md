# Cloth NeXt 2.7.3

Collider Collision now includes a compact Linked Colliders eyedropper and a
member list. Motion, Friction, Collision Gap and Surface Offset directly edit
one scene-owned settings source. Shared [N] and four chain markers identify the
linked values. Role, capture options, frame range, caches and identity stay local.

The one-shot picker preserves selection, the active object and Properties context.
Available Colliders have subtle cyan GPU outlines; hover is stronger. Members of
another group are amber and require a Move confirmation. Invalid or empty clicks
leave the picker active. Success, cancel, shutdown and file/viewport lifecycle
changes tear down temporary overlays and timers without touching scene display data.

Unlink copies current shared values locally. Groups dissolve below two valid
members. Shared settings are resolved by export/bake, motion capture, recovery,
diagnostics and proxy consumers. Linking and editing controls are blocked during Bake.
Read-only library objects and objects used in multiple scenes are not picker targets.

Release to detach now appears inside the red bar, right of the trash icon.

Published only to the configured private repository; historical feeds are unchanged.
The external PPF Contact Solver is not bundled.
