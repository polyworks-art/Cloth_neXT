# Cloth NeXt 2.4.0 Beta

Cloth NeXt 2.4.0 promotes the 2.3.7 development baseline to Beta. It brings
reliable live cache preview, safer lifecycle handling, and the streamlined
Companion to both the Beta and Dev update channels.

## Current-run preview and cache safety

- Rebake and Recovery show the current simulation while retaining the previous
  cache for rollback on cancellation, failure, or add-on shutdown.
- Cache cleanup bounds directory traversal before removing obsolete files.
- Blender file loading clears old viewport shading references safely.

## Welcome and What's New

- First-install Welcome and version-specific What's New remain available in
  the Companion, with manual access from Preferences.
- Onboarding clears stale reload timers and saves seen state only after the
  Companion confirms that its window is ready.
- Packaged onboarding assets reject unsafe Windows paths and external symlinks.

## Unmodified render output

ThreadMark watermarking, its encoder and verifier, bundled models, and Adobe
TrustMark, ONNX Runtime, and BCH dependencies remain removed. Cloth NeXt does
not modify automatic Blender render output files. The bundled Companion
contains Bake, Veyra, Welcome, and What's New modes.

## Release scope

This Beta introduces no new solver functionality compared with 2.3.7. The
external PPF Contact Solver remains unchanged, separately installed, and is
not bundled. Beta packages do not include Dev-only Developer Tools.

## Validation

- Source suite: 1,699 passed; 10 unavailable external-solver cases skipped.
- Local Windows package: all three built-artifact checks, release policy,
  packaged structure, and forbidden solver-material scan passed.
- Blender 5.2.1 LTS: registration, current-run preview, cache rollback and final
  attachment, Unicode file save/reopen, scene/object lifecycle, and Windows
  locked-cache preservation passed in background regression runs.
- Rebuilt Companion: Bake, Welcome, exact-version What's New, invalid-version
  rejection, readiness acknowledgement, and clean process exit passed.
- Interactive undo/redo is outside the background Blender checks.
