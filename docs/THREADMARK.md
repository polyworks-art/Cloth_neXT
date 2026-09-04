# ThreadMark V1 automatic render integration

ThreadMark is an invisible Cloth NeXt render-provenance signal. It is not DRM,
does not identify an artist, account, machine, licence, file, or scene, and is not
guaranteed to survive deliberate adversarial watermark removal.

## Architecture and payload

The Blender-independent `cloth_next.provenance` package contains the public
encoder/decoder/result protocol, versioned product payload, and bounded regional
detection. Pillow/ONNX encoding and atomic image I/O live under the standalone
`verifier` boundary and are not imported by Blender. Blender-side
eligibility is centralized in `should_threadmark_render(scene)` and reuses the
existing owned-playback marker and authenticated cache metadata validator.

ThreadMarkPayloadV1 occupies BCH_SUPER's 40 protected bits:

| Field | Bits | V1 value |
|---|---:|---|
| Product | 16 | `0x434E` (`CN`) |
| ThreadMark schema | 4 | 1 |
| Payload format | 4 | 1 |
| Reserved | 4 | 0 |
| CRC-12/3GPP | 12 | integrity over the preceding 28 bits |

The detector accepts only this exact product/schema/format/reserved/checksum
combination after TrustMark BCH validation. A generic TrustMark decode is never a
Cloth NeXt match.

## Backend and formats

The measured prototype default is TrustMark Q, strength 0.80, BCH_SUPER. Inference
uses pre-provisioned `encoder_Q.onnx` and `decoder_Q.onnx` with ONNX Runtime; it never
imports Torch and never downloads at runtime. Models are retained across calls by a
backend instance. PNG, JPEG, WebP, TIFF, and TIF are supported by atomic file I/O.
Dimensions and alpha are checked before replacement; ICC, EXIF, and DPI are retained
where Pillow supports them. EXR/HDR and unknown formats fail open and remain unchanged.

Detection evaluates at most eight deterministic regions: the full image, centered
90/75/60 percent crops, and four overlapping 70 percent corner regions. Two exact
payload matches produce `DETECTED`; one produces `LIKELY`; low resolution or total
decoder failure produces `INCONCLUSIVE`; otherwise the result is `NOT_DETECTED`.

## Measured benchmark (2026-09-03)

The machine-readable record is `docs/threadmark_benchmark_results.json`. It used
three generated, repository-safe 640x512 images and 20 cases per image: original,
JPEG 95/85/70, resize 75/50/25 percent, crop 10/20/40 percent, brightness, contrast,
gamma, sharpen, blur, noise, three combined attacks, and screenshot simulation.
The same 60 cases were run unmarked as negative controls for every configuration.

| Variant/strength | PSNR dB | SSIM | Mean abs diff | Detected | Detected or Likely | False positives |
|---|---:|---:|---:|---:|---:|---:|
| P / 0.80 | 50.13 | 0.99617 | 0.544 | 12/60 | 13/60 | 0/60 |
| P / 0.90 | 49.14 | 0.99532 | 0.627 | 13/60 | 16/60 | 0/60 |
| P / 1.00 | 48.26 | 0.99436 | 0.710 | 14/60 | 17/60 | 0/60 |
| Q / 0.80 | 44.93 | 0.98493 | 1.122 | 47/60 | 55/60 | 0/60 |
| Q / 0.90 | 43.94 | 0.98187 | 1.273 | 48/60 | 55/60 | 0/60 |
| Q / 1.00 | 43.05 | 0.97869 | 1.424 | 49/60 | 55/60 | 0/60 |

Q/0.80 achieved 3/3 exact detections for original, JPEG 85, resize 50 percent,
and resize+JPEG. Screenshot simulation was 1 Detected and 2 Likely, with all three
valid exact payloads. Its mean cold model load was 0.298 s, mean encode was 0.103 s,
and the mean full eight-region verifier call was 0.226 s. A separate 25-call default
Q/0.80 timing run measured a 0.471 s cold model load, 0.021 s first decode, and
0.0161 s warm individual-region mean (0.0161 s median). Maximum absolute channel
difference was 26/255.

Q/0.90 and Q/1.00 did not improve 20/40-percent crop recovery or screenshot status.
Q/1.00 improved only one combined resize/sharpen/JPEG status and Q/0.90 improved one
25-percent resize status. Under the required priority order, Q/0.80 wins on visual
quality while retaining the same useful screenshot and ordinary-transform recovery.

## Verifier and build

`python -m verifier.app --verify IMAGE --json` provides bounded JSON output. Running
without arguments opens the Tk verifier. `verifier/build_verifier.py --models DIR`
uses the existing one-file/windowed PyInstaller and Cloth NeXt icon conventions.
The build requires pre-provisioned models and cannot download them.

The local evaluation and production build use Adobe CDN binaries with SHA-256
`19b3d1b25836130ffd78775a8f61539f993375d1823ef0e59ba5b8dffb4f892d`
(`encoder_Q.onnx`) and
`ee3268f057c9dabef680e169302f5973d0589feea86189ed229a896cc3aa88df`
(`decoder_Q.onnx`). These hashes identify the evaluated files and the build rejects
any mismatch.

A local, non-published one-file/windowed build succeeded and its CLI returned the
exact payload as `DETECTED`. The final optimized Q-only executable is 104,632,393 bytes;
cold one-file extraction, model load, and eight-region verification took 3.651 s.
The standalone verifier executable and PyInstaller output are ignored development
artifacts. The production Companion embeds the same Q files. Adobe's official FAQ
states that TrustMark is MIT-licensed, permits application integration and
commercial use, describes the models as deployable TrustMark components, and says
they were trained on licensed Adobe Stock images. The packaged extension includes
the complete Adobe copyright and MIT notice in `THIRD_PARTY_NOTICES.md`.

## Blender automatic-render integration

The registered Blender integration evaluates the centralized cache-authenticated
eligibility predicate once at `render_pre`. It snapshots only exact output-path
identities, processes one uniquely changed file at each `render_write`, and dedupes
by `(frame, absolute path)`. It never processes at `render_post`, so one Blender
write cannot be marked twice. Session state contains no Blender Object or Mesh.

The ONNX runtime lives in the owned Companion, outside Blender. A 256-bit random
token authenticates a bounded JSON-line loopback connection. The child starts only
on the first eligible supported write, retains one Q/0.80 backend across animation
frames, and shuts down on render complete, render cancel, file load, or unregister.
Startup, encode, and shutdown are bounded; an exact-child terminate/kill fallback
handles a stuck shutdown. Registration is idempotent and purges stale callbacks.

Covered automatic paths are `write_still=True`, animation frames, Eevee, Cycles,
and background/headless renders in PNG, JPG/JPEG, WebP, and TIF/TIFF. EXR/HDR,
unknown formats, ineligible scenes, and ambiguous final paths are skipped. Any
eligibility, worker, decode, temporary-file, validation, or replace failure logs a
bounded reason and preserves Blender's original output without failing the render.

Blender 5.2.1 runtime validation produced exact `DETECTED` payloads for all six SDR
extensions, two animation frames, Eevee, and one-sample CPU Cycles. Both animation
frames used the same worker PID. Every worker exited with code 0; unregister left
zero ThreadMark handlers and no session worker. EXR created no worker, and an
ineligible PNG remained `NOT_DETECTED`.

## Manual Render Result Save As

**Manual Render Result Save As is unsupported by ThreadMark V1.** The final narrow
Blender 5.2.1 UI spike used the real `IMAGE_OT_save_as` operator after an F12-style
render. On successful save, `Render Result.filepath` and `filepath_raw` changed from
empty values to the exact output path. However, neither type- nor instance-level
message-bus subscriptions fired; `save_pre`, `save_post`, and `save_post_fail` did
not fire; and `WindowManager.operators` exposed no completed operator. The filepath
also persisted unchanged through the next render. Polling it therefore cannot prove
that a new write completed or avoid a race on a same-path save.

Automatic render outputs can carry ThreadMark. A manually saved Render Result
through Blender's Image -> Save As is not covered because Blender 5.2 exposes
neither the Render Result pixel buffer nor a safe post-Save-As Python callback. F12
remains untouched and displays no warning. A future explicit **Save ThreadMarked
Render** action is feasible using supported `save_render` to a selected or temporary
path followed by the existing atomic worker, but it is intentionally not part of
V1 and would add artist interaction.

AgX, Filmic, Standard, and Khronos PBR Neutral automatic PNGs were byte-identical
to immediate Render Result saves. Marking the actual 160x96 UI-saved RGBA probe at
Q/0.80 preserved dimensions and alpha, added no clipping, and measured 55.46 dB
PSNR with a maximum 2/255 RGB-channel change.
