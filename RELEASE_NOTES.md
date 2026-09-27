# Cloth NeXt 2.8.7

Cloth NeXt 2.8.7 makes Resume and unchanged re-bakes more efficient and keeps the Bake window's status honest. Resume now reuses the checkpoint's verified Scene payload instead of capturing animated Colliders and Pins again. Recovery retains the exact encoded Scene by content hash for reliable reuse.

The Bake window's native close button is disabled while work is active so Cancel remains the controlled interruption path. Force-only timeline capture is now identified correctly and no longer appears as an animated Collider export.

Published to the configured private repository. The external PPF Contact Solver is not bundled.
