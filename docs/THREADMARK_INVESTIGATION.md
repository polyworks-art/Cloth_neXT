# ThreadMark investigation record

This record was written before ThreadMark implementation work began. It describes
the repository state inspected on 2026-09-03 and the upstream TrustMark revision
`0ed40cbe8188f664fd9cbbeacd969807de27440a` (2026-04-30).

## Cloth NeXt lifecycle and ownership findings

- `cloth_next/__init__.py` is a deliberately thin extension entry point. The
  canonical version is read from `cloth_next/blender_manifest.toml`; ThreadMark
  must not introduce another version source or change the current version.
- `cloth_next/blender/registration.py` owns the complete registration transaction.
  It applies deterministic `(apply, revert)` steps, rolls back partial registration
  in reverse order, makes repeated `register()`/`unregister()` calls idempotent, and
  shuts down workers, timers, the Bake preview, and the exact owned Companion before
  unregistering RNA and handlers.
- Persistent handler lifecycle is exemplified by
  `cloth_next/blender/validation_state.py`. Each callback carries a stable feature
  marker attribute. Registration removes callbacks carrying that marker from old
  module instances, appends the current callback only if absent, and records no
  Blender datablock references. Unregistration removes every current occurrence,
  purges stale marked callbacks, unregisters its timer, clears observers and cached
  records, and resets its guard. `handler_count()` and real-Blender smoke tests make
  leaks observable.
- `validation_state` is session-only validation state, not proof that a playback
  cache exists. It stores immutable records keyed by object name, clears on file
  load, prunes on undo/redo, and marks enabled deformables dirty from the one shared
  depsgraph callback. ThreadMark eligibility must therefore not equate
  `ValidationState.VALID` with a valid simulated render.
- `cloth_next/blender/solver_test.py` is the current simulation/Bake coordinator.
  A successful Bake publishes PC2 playback and only then attaches or replaces the
  owned playback result. Failure and cancellation abort temporary writers and leave
  the previous complete cache intact. Multi-object playback preflights all members
  and rolls back Blender mutations if the transaction cannot complete.
- `cloth_next/bake/cache_metadata.py` is the authoritative on-disk cache validator.
  A usable result requires a complete schema-1 sidecar, the expected PC2 identity
  and layout, size and SHA-256 match, a valid metadata digest, required object,
  solver, Cloth NeXt, and Blender identities, and (when supplied) matching settings
  and geometry fingerprints. Only `CacheCondition.READY` is usable. Partial,
  missing, corrupt, stale-settings, and stale-geometry results fail closed.
- `cloth_next/blender/playback_cache.py` owns playback classification. Mesh playback
  requires a Mesh Cache modifier plus the Cloth NeXt ownership marker and matching
  recorded cache path; Curve/Rod playback uses the owned data-block cache record.
  Destructive operations use the resolving ownership predicate, while UI-only code
  uses the syscall-free marker predicate. ThreadMark eligibility must reuse these
  predicates and `cache_metadata.inspect_cache`, not duplicate them.
- The settings properties `baked_settings_fingerprint`,
  `baked_geometry_fingerprint`, `baked_fingerprint_version`,
  `baked_cache_condition`, and `baked_metadata_digest` are populated only after a
  successful attachment and cleared with owned playback. They are useful inputs but
  are not sufficient without re-authenticating the current file and attachment.
- The Companion is Cloth NeXt-owned and launched without a shell. Its manager uses
  authenticated local IPC, bounded readiness and shutdown deadlines, timer cleanup,
  graceful close followed by terminate/kill only for the exact owned child, and
  bounded safe deletion of session artifacts. It never acquires authority over an
  external solver. A ThreadMark helper, if used, must extend this ownership model or
  implement an equally explicit feature-owned child lifecycle; it must not become an
  unmanaged daemon.

## Companion, build, test, and packaging findings

- `companion/app.py` is a Tk application with bounded rotating logs and normal-user
  error presentation rather than raw tracebacks. `companion/build_companion.py`
  builds a one-file, windowed PyInstaller executable and deterministically includes
  approved generated assets. Its pinned build dependencies are currently
  PyInstaller and Pillow.
- The standalone verifier is development tooling unless release policy is explicitly
  changed. It must remain outside `cloth_next/`, because the pure-Python extension
  builder recursively packages that directory.
- `tools/build_extension.py` excludes solver/runtime directories and then validates
  and scans every ZIP. Packaged extensions must contain only the validated Bake
  Companion executable and manifest; release-policy tests reject PPF solver material
  and development artifacts. ThreadMark benchmark output, verifier builds/specs, and
  non-runtime models must stay outside the extension tree.
- Unit tests use `tests/fake_bpy.py`; handler idempotency, stale-module purge,
  unregister cleanup, and object-reference release are already covered in
  `tests/test_validation_state.py`. `tools/blender_smoke_test.py` performs repeated
  real-Blender register/register/unregister/unregister cycles and rejects surviving
  handlers, timers, threads, RNA, and UI handles. ThreadMark must be added to both
  layers rather than weakening either gate.
- The normal unit gate is `python -m pytest` (excluding tests marked
  `built_artifact`). Repository tooling also provides extension validation,
  packaging tests, release scanning, compile/import smoke tests, and a real-Blender
  smoke runner.
- Supported Blender begins at 5.0.0. CI smoke coverage uses Blender 5.0.0 and current
  build/release workflows use 5.1.2; code also contains compatibility handling for
  Blender 5.2. The initial conclusion that this workstation exposed Blender 4.2
  only was wrong. The Steam installation described below provides Blender 5.2.1 LTS.
- No existing Cloth NeXt render handler or Render Result mutation subsystem exists.
  Adding one is a new lifecycle surface, not an extension point already proven by
  the repository.

## TrustMark evaluation findings

- Adobe TrustMark source is MIT licensed. Any copied or redistributed substantial
  source must retain Adobe's copyright and MIT permission notice.
- The official Python package is PyTorch-based (`torch`, `torchvision`, Lightning,
  OmegaConf, NumPy, and Einops). It imports Torch at module load and downloads model
  configuration/checkpoint files on first use. It is therefore unsuitable for
  direct import into Blender and violates ThreadMark's offline/no-render-download
  requirements unless isolated and pre-provisioned.
- Official ONNX inference exists in the JavaScript decoder and Rust encode/decode
  implementation. The Rust implementation supports binary payloads and every ECC
  mode. P and Q each use a 256-pixel encoder; P uses a 224-pixel decoder and forced
  square crop, while Q uses a 256-pixel decoder and only square-crops extreme aspect
  ratios. Images encoded by one variant cannot be decoded by another.
- TrustMark carries 100 model bits. `BCH_SUPER` exposes 40 protected payload bits,
  56 ECC bits, and four schema bits, correcting up to eight bit flips. This is the
  required initial ThreadMark capacity. P is documented as the highest-visual-quality
  variant (typical PSNR 48-50); Q is the default quality/robustness tradeoff (typical
  PSNR 43-45). Those upstream figures are context only and are not Cloth NeXt
  benchmark results.
- The official source fetches `encoder_{P,Q}.onnx` and `decoder_{P,Q}.onnx` from
  Adobe's CAI model CDN. These binaries are not committed to the MIT repository. The
  checked-out repository contains no separate model license, redistribution grant,
  checksums for the ONNX files, or provenance manifest for them. Models may be used
  for an isolated evaluation only after download and hashing; they must not be
  vendored or shipped until redistribution terms are confirmed.
- The official bounding-box detector currently exists in the PyTorch implementation
  and adds another large model. It is not part of the official Rust ONNX subset.
  A bounded deterministic crop strategy should be benchmarked before accepting that
  dependency and runtime cost.

## Windows Blender discovery correction

Discovery was repeated without relying on directory names or PATH precedence:

- `where.exe blender` and `Get-Command blender -All` returned no executable.
- No repository setting or environment variable selected a Blender executable.
- The HKLM Valve/Steam registry entries and `%PROGRAMFILES(X86)%\Steam` resolved to
  `C:\Program Files (x86)\Steam`. `%PROGRAMFILES%\Steam` was absent.
- `steamapps\libraryfolders.vdf` was parsed and contained the same configured Steam
  library. Every configured library was checked for
  `steamapps\common\Blender\blender.exe`.
- The selected executable was
  `C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe`.
  Running that exact path with `--version` reported **Blender 5.2.1 LTS**, build
  `9e2066aef7ef`, built 2026-08-25 02:38:20.
- `C:\Program Files\Steam\steamapps\common\Blender\blender.exe` was absent. The
  Blender Foundation directory did not yield another executable candidate during
  the corrected recursive executable search.

Steam configuration, Blender preferences, global PATH, and installed versions were
not changed. Test runs used `--factory-startup`, absolute executable paths, and
temporary user-resource directories. The repository registration smoke command was:

```text
python tools/run_blender_smoke.py --blender "C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
```

It passed repeated register/register/unregister/unregister checks on 5.2.1 with no
surviving Cloth NeXt handlers, timers, threads, RNA, or UI handles.

## Blender 5.2.1 render spike

`tools/blender_threadmark_render_probe.py` was run headlessly with the absolute Steam
executable. `tools/blender_threadmark_ui_probe.py` ran the UI render operator in
`INVOKE_DEFAULT` mode, which is the F12 operator path, and then Blender's actual
`IMAGE_OT_save_as` operator in a Render Result Image Editor context.

Observed handler order was:

| Case | Observed order |
|---|---|
| Eevee `bpy.ops.render.render()` | pre -> post -> complete |
| Eevee `write_still=True` | pre -> post -> write -> complete |
| Eevee two-frame animation | pre -> post -> write, per frame; one complete at end |
| Cycles CPU `write_still=True` | pre -> post -> write -> complete |
| no-camera failure | pre -> post -> cancel; operator raised |
| repeated no-write renders | pre -> post -> complete each time |
| UI/F12-equivalent render | pre -> post -> complete |

For file renders, the PNG already existed with its final hash at `render_post`; the
same file was present at `render_write` and `render_complete`. Animation frame 0001
existed at its post/write callbacks before frame 0002 began, and the final complete
callback reported frame 1 after both files existed. `render_write` never fired for
the no-write or UI/F12-equivalent render. Both Eevee and one-sample CPU Cycles
completed successfully. Failure did not leak callbacks. All probe handler counts
were zero after explicit removal.

Blender 5.2's special `Render Result` image is not a normal mutable image datablock.
In both background and foreground tests it reported type `RENDER_RESULT`, eight
slots, `size == (0, 0)`, zero accessible pixels, no `layers` attribute, and no
`views` attribute. In the foreground it changed to `has_data == true` after post,
but still exposed no pixels. `Image.save_render` could nevertheless write it, and a
save immediately after `write_still` was byte-identical to Blender's automatic PNG.
The real `Image > Save As` operator also returned `FINISHED` and wrote the UI Render
Result. This proves the manual workflow works, but provides no supported buffer that
ThreadMark can replace before the user saves it.

The safe automatic-file boundary is after Blender has applied AgX/Filmic/custom
display transforms and encoded the requested SDR output. Processing that completed
file avoids applying a second view transform. The atomic image pipeline preserves
dimensions, copies the untouched alpha plane, reopens and validates the temporary
file, and only then replaces the sibling output. EXR/HDR is explicitly unsupported.
It cannot, however, make UI `Image > Save As` use marked pixels because that route
does not call `render_write` and the Render Result has no writable pixel buffer.

This boundary was checked separately with AgX, Filmic, Standard, and Khronos PBR
Neutral. For every transform, Blender's automatic PNG and an immediate
`Render Result.save_render` PNG had identical SHA-256 values. A Q/0.80 mark applied
to the actual 160x96 UI-saved transparent PNG preserved 160x96 dimensions and a
byte-identical alpha plane, introduced no new clipped channel samples, measured
55.46 dB PSNR, 0.184/255 mean absolute RGB difference, and 2/255 maximum RGB
difference. The deliberately tiny result was conservatively `LIKELY` with one of
one eligible regions matching at 0.9839 confidence rather than being overstated as
`DETECTED`.

A true user-cancel experiment remains unproven. Blender's `render.view_cancel`
operator returned `FINISHED` only after the render had already completed because
Python timers did not run during the modal Cycles render. A Windows Escape-input
attempt was aborted without sending input when the controller could not uniquely
expose the probe window alongside an existing artist Blender session. Raw Win32
injection or calling Blender RNA from a background Python thread was rejected as an
unsafe substitute. The measured no-camera failure does establish that Blender calls
`render_cancel` after post on this failure path, but it is not represented as a user
cancellation result.

## Final manual-save observation result

The final narrow foreground spike invoked Blender's real `IMAGE_OT_save_as` with
`copy=False` and `save_as_render=True` after an F12-equivalent render. Before the
save, Render Result `filepath` and `filepath_raw` were empty; after the successful
write, both exposed the exact selected path. No type-level or image-instance
message-bus subscription fired. None of `save_pre`, `save_post`, or
`save_post_fail` fired, and `WindowManager.operators` remained empty before and
after. A second render left the filepath unchanged.

The property is therefore historical state, not a completion event. Polling cannot
distinguish a new same-path save or establish that Blender has closed the file, so
it cannot meet the no-race requirement. **Manual Render Result Save As is
unsupported by ThreadMark V1.** Direct Render Result mutation and unsupported
operator interception remain rejected.

## Implemented automatic boundary

Automatic PNG/JPG/JPEG/WebP/TIF/TIFF writes now use only `render_pre`,
`render_write`, and terminal lifecycle handlers. Eligibility is evaluated once;
each write must resolve to exactly one changed output identity; and `(frame,
absolute path)` deduplication prevents repeat processing. An authenticated owned
Companion child retains the Q/0.80 ONNX backend for an animation and is released on
complete, cancel, load, or unregister. EXR, ineligible scenes, ambiguous paths, and
all failures preserve Blender's original output.

The Blender 5.2.1 headless integration run detected the exact V1 payload in every
supported extension, both animation frames, Eevee, and CPU Cycles. The animation
reused one PID; all children exited 0; EXR started no child; the ineligible output
remained unmarked; and unregister left no handler or worker. The later release audit
uses Adobe's current official FAQ as the distribution basis: it describes TrustMark
as MIT-licensed, permits application and commercial integration, identifies the
models as deployment components, and states that they were trained on licensed
Adobe Stock images. The complete Adobe copyright and MIT permission notice is now a
mandatory source and packaged-artifact file.
