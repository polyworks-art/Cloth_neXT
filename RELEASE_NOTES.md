# Cloth NeXt 2.3.6 Dev

Cloth NeXt 2.3.6 introduces automatic ThreadMark provenance for eligible SDR
renders, adds lifecycle-safe Welcome and What's New screens, and fixes stale
viewport-shading references across Blender file loads. This is a Dev release for
validation before the next Beta.

## Automatic ThreadMark provenance

- Eligible automatic PNG, JPEG, WebP, and TIFF stills and animation frames receive
  the measured TrustMark Q/0.80 Cloth NeXt V1 payload after Blender finishes each
  file write. Eevee, Cycles CPU, and background rendering are covered.
- One authenticated, owned Companion child loads the offline ONNX models lazily and
  is reused across an animation. Complete, cancel, file load, and unregister all
  shut it down without a permanent service.
- Encoding is fail-open: unsupported EXR/HDR, ambiguous paths, ineligible scenes,
  or any processing failure preserve Blender's original render.
- Blender's manual Render Result -> Image -> Save As remains intentionally
  unsupported because Blender 5.2 exposes no reliable post-save callback. F12 is
  not modified and shows no warning.

## Welcome and What's New

- A first installation opens one compact Welcome screen from the existing
  Companion; later unseen versions open their curated What's New screen once.
- Seen state is bounded and monotonic across restarts, downgrades, and channel
  changes. Manual Preferences actions do not alter automatic state.
- All copy and artwork is packaged offline, validated against the exact release
  version, and rendered without adding another executable or background service.

## Reliability and packaging

- Viewport shading references are cleared before Blender replaces file data,
  preventing stale RNA access during file-open lifecycle changes.
- The Companion build embeds only the hash-pinned TrustMark Q encoder and decoder,
  includes the Adobe MIT notice, and performs no runtime download.
- The normal repository suite passes 1,725 tests, with 10 configured external
  solver integration cases skipped honestly and 3 built-artifact cases reserved
  for the publication build. Blender 5.2.1 runtime tests cover every supported
  format, two-frame worker reuse, Eevee, Cycles, headless execution, EXR skip,
  ineligibility, process cleanup, and registration cleanup.

The external PPF Contact Solver is unchanged, remains a separate installation,
and is not bundled with Cloth NeXt.
