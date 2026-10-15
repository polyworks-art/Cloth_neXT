# GAIA Water Flow bounded streaming audit

Unpublished development after 2.9.9, verified on Windows/CUDA with official GAIA 0.23 (`2026-09-27-20-44`). No release is published. Spatial reconstruction, support, coordinate conversion and field values remain as validated in the [resolution audit](GAIA_WATER_FLOW_RESOLUTION_AUDIT.md).

## Production contract

Water Flow production uses **current + next source frame**, never the entire animation. The last source frame is a one-sample window when addressed directly. Each source field is a checksummed member of the existing `.gaia` ZIP container, accessed through its index without decoding preceding frames. No FLIP reconstruction runs in the streaming helper. Preparation remains the existing bounded main-thread capture/background reconstruction operation.

The old whole-schedule **32 MiB** velocity limit remains unchanged. A separate **128 MiB active values** budget covers all Water Flow windows plus preserved base grids. This is a window allocation limit, not a promise that total process RSS is 128 MiB: decoded support, conversion intermediates, frontend staging and native state also consume memory. No prefetch queue is added: measured reading/encoding is a small part of the bake, and an extra decoded frame would increase the resident footprint. Temporary arrays are released before integration continues; weak-reference tests cover both 10 and 250 frames.

Water-disabled scenes return through the existing bake path before opening a cache, launching a helper or adding holds. Group weights and targeting remain unchanged for other objects; existing Force Field scripts and grids are preserved when replacing Water Flow sources. Shared target material groups remain rejected.

## Exact held/reload sequence

1. Export/build the scene through the existing owned control server. The Water Flow descriptor is CNX-owned metadata, not an unknown solver parameter.
2. The process manager attaches the official-Python helper to its existing Windows kill-on-close job, or a separately tracked POSIX process group. Only then does it publish the helper startup gate.
3. Recover the built official session through `App.recover` and the public project pointer. Fresh runs call `run_until_frame(0)` and confirm `held_frame()`.
4. Read/validate the two samples for the **actual** held boundary. Encode the existing axis conversion and strength multiplication without changing field values.
5. Call public `ForceField.grid(..., kind='air-velocity', times=...)`, then public `update_force_field`. Official GAIA writes its grid files and `inputs_updated` while held. Its documented contract consumes the updated sources before the next integration step.
6. Clear all frontend/reader temporary arrays. Call `run_until_frame(boundary + 1)`; confirm the actual new hold or actual final output completion. The process, device buffers, positions and integration state persist. There is no Python roundtrip per internal substep.
7. Read outputs through the existing validated output map/frame decoder and PC2 sink. Existing Bake progress reports the corresponding Water Flow source frame.

GAIA can overshoot requested output boundaries with a large integration step. The actual held boundary is authoritative; a skipped output boundary must not cause an obsolete window to be uploaded. The numerical overshoot control below verifies this behavior.

### Resume-specific public behavior

Official 0.23's automatic `start -> resume -> start` clears the pending hold during its second start. The initial integration can consequently precede a new field upload. The production resume adapter therefore uses the **public native CLI** `--path ... --output ... --load <verified checkpoint>` and the documented `hold_at_frame` protocol. It installs the checkpoint hold atomically before launching the native child, clears the owned stale held/reload/save-and-quit flags and confirms the new hold before uploading. Subsequent updates use the public frontend methods above. It does not change solver source or private frontend state. The child inherits the existing helper ownership and is reaped by the same process manager.

The official frontend's `is_running` detection is global. A separate active GAIA simulation is rejected rather than adopted or cancelled. GPU integration probes must run serially. The owned control server is monitored for disconnect, but its native-run status adoption is not polled during the external held sequence: that adoption assumes the server's own run script and can misclassify a healthy held run. Output transfer, validation and finalization still use the existing server protocol.

## Authoritative temporal mapping and validation

For Cloth range `[first, last]`, FPS `f` and actual solver output boundary `b`:

`source frames = [first + b, first + b + 1]`

`solver sample times = [b / f, (b + 1) / f]`

The final sample is clipped to `last`. For cache 50–300 and Cloth 120–200, boundary zero reads source 120/121 at solver times 0/1-FPS; boundary 20 reads 140/141. Container source times remain relative to the cache's original first frame and are checked independently. Native interpolation between current/next samples stays active.

Validation checks the canonical recipe fingerprint, FPS, contiguous unique frame identity, source time, physical units/field format, dimensions, bounds, array names, byte lengths/dtypes, per-array checksums, finite velocity/support values and the existing node layout. A corrupt requested sample reports its source-frame identity and fails the bake; the old field is never released to continue indefinitely. Cache fingerprints and streaming descriptors participate in the existing recovery parameter identity.

## Failure, cancellation and existing Recovery

On read/upload/observer failure or cancellation, an owned held run receives public `save_and_quit`. A confirmed hold is persisted before saving, including cancellation that races native completion of a step. The helper has a bounded graceful exit; the existing manager terminates/reaps its complete owned tree if it stalls. A server disconnect follows that same teardown. Foreign runs are never signalled.

The existing Recovery metadata stores the last completed boundary, active source window, next source frame and cache fingerprint. Resume derives its boundary from an authenticated native checkpoint, never a UI progress counter. Existing partial-PC2 validation and completed-frame handling remain in use. Start Fresh clears simulation/recovery streaming state while keeping the prepared environmental `.gaia` cache reusable.

The Windows integration uncovered an existing recursive-delete bug: a short project root can have descendants beyond MAX_PATH, which Python 3.13's walker skips as missing. Recursive authenticated deletion now prefixes the root for long-path I/O unconditionally. Ownership checks remain unchanged. A real long-descendant regression covers this correction.

## Real and numerical verification

| Check | Result |
| --- | --- |
| Changing 5-frame whole schedule versus streamed, resolution 4, dt 0.001 | Bit-identical trajectories; maximum error 0 |
| Changing 5-frame whole schedule versus streamed, **resolution 100**, dt 0.001 | Maximum error **6.27e-15 m**; whole 22.544 s, streamed 23.186 s |
| Overshooting integration, resolution 4, dt 0.07 at 24 FPS | Maximum error **3.73e-9 m**, below the predeclared 2e-6 m control tolerance |
| Real supplied source, Auto 100 x 100 x 50, **all 250 frames** | Production SolverSession completed, 250-frame PC2 with 9 vertices, finite validated outputs, correct final source 250 and successful owned cleanup |
| Real 250-frame production elapsed time | **1030.10 s** (17 min 10 s), including launch/build/output/cleanup |
| Real source 140–149, 10-frame production | Passed; final active source pair 148/149, solver times 8/24 and 9/24, complete 10-frame PC2 |
| Blender cancel → new Blender process → Resume | Passed on changing source 120–124, 1024 vertices; checkpoint 3 resumed with source **123/124**, complete 5-frame PC2 and absent owned alias/target after cleanup |
| Blender Start Fresh and repeated Start Fresh/rebake | Passed; new launch, complete output, cleanup and identical environmental cache SHA before/after each operation |
| Resume versus Start Fresh, extra 1024-vertex comparison | Maximum difference **2.50e-6 m**; repeated independent Fresh runs differ by **1.79e-6 m**, demonstrating float32/GPU repeatability scale. This is separate from the strict whole-versus-stream numerical controls above. |

The long test uses the supplied real FLIP-derived velocity/support sequence with a representative small cloth through the actual production session/frame/PC2 lifecycle, rather than the whole user scene. The supplied `.blend` is loaded read-only for preparation and never saved. Asynchronous Auto preparation of source frames 1–250 took 161.15 s and produced `real-100-250.gaia` (150,453,307 bytes).

## Transport capability

These are **actual unchanged native CUDA runs**, not serializer-only estimates:

| Longest-axis resolution | Supplied-aspect dimensions | Two-sample velocity bytes | Official held update |
| --- | --- | ---: | --- |
| Auto/Medium 100 | 100 x 100 x 50 | 12,000,000 | Passed, including real 250-frame production |
| High 150 | 150 x 150 x 75 | 40,500,000 | Passed public API and actual production helper/PC2/cleanup, two changing windows |
| Extreme 200 | 200 x 200 x 100 | 96,000,000 | Passed public API and actual production helper/PC2/cleanup, two changing windows |

Extreme is not silently lowered or made preparation-only for the supplied aspect ratio. Other bounds/aspect ratios and multiple simultaneous Water Flow targets can exceed the combined 128 MiB budget; those fail explicitly. These results certify the tested official Windows/CUDA build, not every backend/platform. The old schedule adapter still rejects payloads above its original 32 MiB cap.

## Performance and memory

Real Auto/100 source 140–149, nine streamed intervals:

| Work | Total | Mean per two-sample window |
| --- | ---: | ---: |
| ZIP read/inflate | 0.29955 s | 33.28 ms |
| Checksum/decode/validation | 0.07126 s | 7.92 ms |
| Axis encoding/strength | 0.17542 s | 19.49 ms |
| Public field export/reload signalling | 0.07539 s | 8.38 ms |
| Native advancement + public hold wait/observation | 46.12361 s | 5.12485 s |

The first four operations total **0.62162 s** over a 60.36 s instrumented run. Read/inflate and CPU conversion are not the dominant bake cost. Per decoded source-frame averages are approximately half the two-sample figures; windows deliberately reread the overlapping sample rather than retain a decoded prefetch cache.

The controlled resolution-100 whole-versus-stream run measures a **0.642 s net wall-time difference (2.85%)** for four intervals. This includes reload/hold observation and normal launch/timing variance; it is not a separately instrumented native GPU hold duration. The advancement timing above includes integration and official process-state polling, so it must not be labelled pure streaming overhead.

High/150's two changing windows averaged 48.49 ms read/inflate, 26.62 ms decode/validation, 70.28 ms encode and 112.33 ms public export/signalling. Its dedicated run took 31.50 s while CPU regression/integrity work was active. Extreme/200's earlier dedicated run took 9.20 s with exports of 122.64 and 115.71 ms; this is a capability measurement, not a controlled speed comparison to High.

Subsequent actual **production** tests include normal launch/build/validated PC2/owned cleanup:

| Production check | 150, 3 source frames | 200, 3 source frames |
| --- | ---: | ---: |
| Total instrumented wall time | 19.78 s | 20.65 s |
| Read/inflate per two-sample window | 46.69 ms | 113.58 ms |
| Decode/validate per window | 26.61 ms | 62.63 ms |
| Encode per window | 70.22 ms | 164.14 ms |
| Public export/signalling per window | 37.98 ms | 90.80 ms |
| Streaming helper peak RSS | **150.34 MiB** | **273.46 MiB** |
| Native CUDA peak RSS | 217.73 MiB | 335.05 MiB |

The production orchestration process stayed about 40.3 MiB at both resolutions. These peak measurements make the difference between the active-values budget and total working RSS explicit.

| Peak RSS | 10 frames | 250 frames |
| --- | ---: | ---: |
| Independent Auto/100 reader process | **67.02 MiB** | **67.48 MiB** |
| Production orchestration process | 41.52 MiB | 42.56 MiB |
| Official-Python streaming helper | 82.46 MiB | 91.05 MiB |
| Native CUDA solver | 182.62 MiB | 187.12 MiB |
| Control server | 21.72 MiB | 21.70 MiB |

Reader iteration releases every returned window; final reader RSS is about 37 MiB in both cases. The production 10-frame run uses the same full 250-frame source container, so index size is held constant. Small native/helper variation reflects runtime state/allocator peaks, not retention of an animation-sized velocity schedule.

Peak Blender RSS is measured separately in the real 1024-vertex cancel/resume/Fresh integration (approximately 216–227 MiB in the initial measured cycle; exact final-cycle bytes are in the reports). The 250-frame production harness is a standalone orchestration process, **not a 250-frame Blender RSS measurement**. Blender does not decode streaming grids; the owned helper does. GPU/device memory was not independently measured.

## Integrity, regression and reproduction

The pinned official ZIP SHA-256 is `fd3f43aa7e9849526b5113da558443a074039231ba700edfc953b6c83eb990d4`. All **14,606 files** in both the selected installed package and the workspace audit copy matched that ZIP byte-for-byte. Solver source, binaries and frontend are unchanged. Workspace-local extraction avoids a Windows filesystem access boundary encountered with external-directory aliases; the canonical project resolver now matches the official `.git`-presence guard rather than accidentally using the enclosing repository's Git branch.

Final full regression: **2,115 passed, 19 skipped, 3 deselected** in 139.21 s, recorded in `dist/gaia-streaming/full-regression-final.log`. Source extension validation and Python compilation pass. Tests cover disabled export, one/final sample mapping, two-sample interpolation encoding, changing windows, non-frame-1 offsets, corrupt/missing cache identity, cancellation/failure while held, cancelled-step races, stale resume markers, recovery identity, Fresh reset, direct indexed reads, bounded iteration and released array references. Platform-dependent skips are reported explicitly.

Reproduction tools:

- `tools/blender_prepare_water_stream_audit.py`: real asynchronous FLIP preparation, using Blender.
- `tools/run_gaia_water_stream.py`: actual production session/PC2 run and parent/descendant RSS sampling, using official Python.
- `tools/audit_gaia_held_stream.py`: whole-versus-stream numerical controls and real 150/200 update capability; run from the official package root with its Python.
- `tools/audit_gaia_stream_memory.py`: independent 10/250-frame Auto reader memory/timing probes.
- `tools/blender_water_stream_recovery.py`: existing Blender recovery gate with changing prepared Water Flow, run in separate processes for `cancel`, `resume`, `fresh`.

The audit data container is `dist/GAIA_Water_Streaming_Audit.gaia`. It contains prepared caches, numerical/performance/recovery reports, regression logs and PC2 evidence. It excludes the official runtime and the user's original scene/FLIP particle cache.
