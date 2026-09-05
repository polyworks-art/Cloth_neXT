# Cloth NeXt 2.3.7 Dev

Cloth NeXt 2.3.7 removes ThreadMark and restores untouched Blender render output.
This is a Dev release for validation before the next Beta.

## Preview and reliability fixes

- Rebake and Recovery switch live preview to the current run. Previous caches
  remain recoverable and are restored on cancellation, failure, or shutdown.
- Cleanup now bounds directory traversal, avoiding an unbounded scan at Bake start.
- Onboarding cleans stale reload timers and waits for a real UI-ready confirmation
  before saving seen state. Windows asset path escapes are rejected.

## Watermarking removed

- Cloth NeXt no longer embeds invisible provenance signals in automatic PNG,
  JPEG, WebP, or TIFF output.
- All ThreadMark Blender handlers, payload and detection modules, model files,
  encoder-worker support, verifier sources, tests, probes, and benchmark artifacts
  have been removed.
- Adobe TrustMark, ONNX Runtime, and BCH are no longer build or runtime
  dependencies.
- The Companion contains only Bake, Veyra, Welcome, and What's New modes and is
  substantially smaller.

## Retained improvements

Validation: 1,699 tests passed; 10 external-solver cases skipped and three
built-artifact checks run separately. Blender 5.2.1 verifies real preview geometry,
rollback and final attachment for rebake, cancelled caches and recovery partials.

- First-install Welcome and version-specific What's New screens remain available
  through the existing Companion.
- Viewport shading references are still cleared safely before Blender replaces
  file data.
- Release policy continues to validate exact-version onboarding content, package
  integrity, GitHub's blob-size ceiling, and the absence of external solver files.

The external PPF Contact Solver is unchanged, remains a separate installation,
and is not bundled with Cloth NeXt.
