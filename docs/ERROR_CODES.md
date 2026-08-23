# Cloth NeXt Bake error codes

This page is generated from the runtime registry in
`cloth_next/core/error_codes.py`, the canonical source for public Cloth NeXt
error identifiers. Existing identifiers remain stable; the broad `x00` code
in each stage is retained for failures whose specific cause cannot be proven.

The Bake Companion shows the code with concise recovery guidance. It may
request updated action text from
`https://polyworks-art.github.io/Cloth_neXT/errors/errors.json`; only the code
selects an entry. Scene data, filenames, and diagnostic logs are not uploaded.
If the request fails, the guidance bundled with the installed build remains.

| Code | Stage | Cause | First action |
| --- | --- | --- | --- |
| `CNX-E100` | Scene validation | Unclassified scene validation failure | Correct the highlighted scene setting and retry. |
| `CNX-E101` | Scene validation | No enabled deformable object | Enable Cloth NeXt on at least one supported Mesh or Curve. |
| `CNX-E102` | Scene validation | Invalid or inconsistent Bake range | Use a valid common Bake range on all deformables. |
| `CNX-E103` | Scene validation | Unsupported or changing topology during preparation | Apply topology-changing modifiers or keep evaluated topology constant. |
| `CNX-E104` | Scene validation | Invalid material or solver-quality values | Correct the highlighted material or quality value. |
| `CNX-E105` | Scene validation | Invalid Pin group or Pin animation | Check the Pin vertex group, mode, and animated target topology. |
| `CNX-E106` | Scene validation | Incompatible multi-object settings | Give all deformables a compatible range and supported configuration. |
| `CNX-E107` | Scene validation | Object disappeared during preparation | Restore the named object and do not delete it while Bake is starting. |
| `CNX-E108` | Scene validation | Invalid Force configuration | Use a supported Force on an Empty and verify its animated values. |
| `CNX-E109` | Scene validation | Non-finite or malformed geometry | Repair NaN/Inf coordinates, degenerate geometry, or invalid transforms. |
| `CNX-E110` | Companion startup | Unclassified Companion startup failure | Close stale Bake windows and retry. |
| `CNX-E111` | Companion startup | Companion bundle missing or failed integrity validation | Repair or reinstall the Cloth NeXt extension. |
| `CNX-E112` | Companion startup | Local Companion transport could not start | Close stale Bake windows and retry; check local security software. |
| `CNX-E113` | Companion startup | Companion process launch failed | Check Windows execution permissions, then repair or reinstall the extension. |
| `CNX-E114` | Companion startup | Companion readiness handshake timed out | Close the existing Bake window and retry. |
| `CNX-E115` | Companion startup | Companion window was not visible or topmost | Restore normal desktop/window access and retry. |
| `CNX-E116` | Companion startup | Companion authentication or protocol mismatch | Close all Bake windows and reinstall the matching extension build. |
| `CNX-E120` | Bake preparation | Unclassified preparation or export failure | Check evaluated geometry and the cache folder, then retry. |
| `CNX-E121` | Bake preparation | Cache/work directory is missing or not writable | Choose a writable cache location and check free disk space. |
| `CNX-E122` | Bake preparation | Evaluated geometry export failed | Check modifiers, transforms, and evaluated object geometry. |
| `CNX-E123` | Bake preparation | PPF scene or parameter encoding failed | Check geometry, materials, Pins, Forces, and finite numeric values. |
| `CNX-E124` | Bake preparation | Animated Pin target capture failed | Keep pinned topology and objects unchanged throughout the Bake range. |
| `CNX-E125` | Bake preparation | Insufficient disk space | Free disk space or move the cache to a larger writable volume. |
| `CNX-E126` | Bake preparation | Bake worker could not start | Retry after other heavy jobs finish; restart Blender if thread creation keeps failing. |
| `CNX-E127` | Bake preparation | Interrupted or stale partial Bake state | Clear the partial result or Rebake; the last complete cache remains safe. |
| `CNX-E128` | Bake preparation | Animated Collider capture failed | Keep the Collider's evaluated topology stable and repair the reported frame or transform. |
| `CNX-E129` | Bake preparation | Recovery checkpoint is missing, corrupt, or incompatible | Choose a compatible verified checkpoint, or use Start Fresh after preserving needed files. |
| `CNX-E130` | Solver startup | Unclassified solver startup failure | Run the solver health check and repair the managed solver. |
| `CNX-E131` | Solver startup | Solver executable, native worker, or runtime dependency missing | Restore or repair the verified solver; if security software quarantined it, allow the solver folder. |
| `CNX-E132` | Solver startup | Solver protocol, schema, or package is incompatible | Install the solver version required by this Cloth NeXt release. |
| `CNX-E133` | Solver startup | Solver health or readiness check failed | Repair the managed solver and inspect its startup diagnostics. |
| `CNX-E134` | Solver startup | Solver process exited during startup | Inspect the solver stderr tail; update GPU drivers or repair dependencies. |
| `CNX-E135` | Solver startup | Solver execution permission denied | Allow the signed solver executable and retry. |
| `CNX-E136` | Solver startup | Configured local solver port is occupied by another service | Close the conflicting service or choose another port; Cloth NeXt will not stop foreign processes. |
| `CNX-E140` | Scene upload | Unclassified scene upload failure | Check the local solver connection and retry. |
| `CNX-E141` | Scene upload | Solver connection or response timed out | Retry and check whether security software blocks localhost traffic. |
| `CNX-E142` | Scene upload | Solver connection closed or broke | Retry after the solver health check succeeds. |
| `CNX-E143` | Scene upload | Solver rejected or did not acknowledge upload | Inspect the diagnostic log and verify the matching solver version. |
| `CNX-E144` | Scene upload | Uploaded payload hash or identity mismatch | Repair the solver installation and retry with a fresh Bake. |
| `CNX-E145` | Scene upload | Malformed or oversized solver response | Repair or update the solver to the supported protocol version. |
| `CNX-E146` | Solver connection | PPF control server exited while an owned solver descendant remained active | Keep the diagnostic log and retry after Cloth NeXt confirms owned-process cleanup. |
| `CNX-E150` | Project build | Unclassified solver project build failure | Inspect scene geometry and the solver diagnostic log. |
| `CNX-E151` | Project build | Solver rejected project build | Inspect geometry, materials, Pins, and Forces in the diagnostic log. |
| `CNX-E152` | Project build | Project build timed out | Simplify the scene or increase stability/performance headroom, then retry. |
| `CNX-E153` | Project build | Contact or geometry initialization failed | Repair intersections, degenerate faces, and invalid collision geometry. |
| `CNX-E154` | Project build | Solver project was unexpectedly busy | Wait for the owned solver to stop, then retry. |
| `CNX-E160` | Simulation | Unclassified simulation failure | Inspect the failing frame and solver diagnostic log. |
| `CNX-E161` | Simulation | Constraint solver did not converge | Lower Friction first. If it still fails, reduce Pressure and Collision Gap, increase animated Collider sampling, then try a smaller Time Step. |
| `CNX-E162` | Simulation | Intersection blocked the simulation from advancing | Separate intersecting geometry. If it fails while inflating or self-colliding, lower Pressure, add clearance between layers, or raise solver quality with a smaller Time Step. |
| `CNX-E163` | Simulation | Simulation stalled or timed out | Inspect the last frame, reduce scene complexity, and retry. |
| `CNX-E164` | Simulation | Solver process crashed or exited | Inspect the solver stderr tail for the underlying cause (intersection, non-finite, or convergence) before checking GPU/driver stability. |
| `CNX-E165` | Simulation | Non-finite simulation result | Reduce Time Step and extreme Forces/stiffness; repair invalid input geometry. |
| `CNX-E166` | Simulation | RAM safety limit or solver memory exhaustion | Lower scene complexity; raise the RAM Auto Cancel threshold only when sufficient memory is available. |
| `CNX-E167` | Simulation | Solver completed without every requested frame | Keep the diagnostic log and retry after a solver health check. |
| `CNX-E168` | Simulation | Force or parameter instability | Reduce animated Force magnitude and extreme material parameters. |
| `CNX-E169` | Simulation | Unexpected solver state | Stop other solver work and retry with a fresh owned project. |
| `CNX-E170` | Result transfer | Unclassified result transfer failure | Check disk space and the local solver connection. |
| `CNX-E171` | Result transfer | Result transfer timed out | Check disk performance and localhost security software, then retry. |
| `CNX-E172` | Result transfer | Connection broke during result transfer | Retry after a successful solver health check. |
| `CNX-E173` | Result transfer | Invalid or missing solver output map | Keep the diagnostic log and repair/update the solver. |
| `CNX-E174` | Result transfer | Requested result frame is missing | Retry; report the diagnostic log if the same frame is missing again. |
| `CNX-E175` | Result transfer | Result frame is corrupt or cannot be decoded | Check disk integrity and repair/update the solver. |
| `CNX-E176` | Result transfer | Result size or vertex count mismatch | Restore the original topology and Rebake. |
| `CNX-E180` | Playback cache | Unclassified playback-cache failure | Check cache access and target topology. |
| `CNX-E181` | Playback cache | Playback cache could not be written | Choose a writable cache folder with sufficient free space. |
| `CNX-E182` | Playback cache | PC2 finalization failed | Check disk space and filesystem reliability, then Rebake. |
| `CNX-E183` | Playback cache | Object topology changed before import | Restore the topology used when the Bake began and Rebake. |
| `CNX-E184` | Playback cache | Target object no longer exists | Restore the target object and Rebake. |
| `CNX-E185` | Playback cache | Playback attachment failed | Remove conflicting cache modifiers or animation and retry. |
| `CNX-E186` | Playback cache | Cache integrity validation failed | Do not use the partial cache; Rebake to create a verified result. |
| `CNX-E187` | Playback cache | Multi-object cache set is incomplete or inconsistent | Keep every target object unchanged and Rebake the full set. |
| `CNX-E188` | Playback cache | Rod Curve playback could not be applied | Restore the original Curve topology and remove conflicting Curve animation. |
| `CNX-E190` | Cleanup | Unclassified cancellation cleanup failure | Wait for cleanup; restart Blender only if the owned process remains stuck. |
| `CNX-E191` | Cleanup | Cancellation did not complete in time | Wait briefly, then restart Blender if the owned solver remains active. |
| `CNX-E192` | Cleanup | Owned solver process could not be stopped | Close Blender after preserving diagnostics, then retry. |
| `CNX-E193` | Cleanup | Temporary or partial files could not be removed | Close programs using the cache folder and clear the partial result. |
| `CNX-E198` | Internal | Bake worker stopped without a terminal result | Preserve diagnostics and restart Blender before retrying. |
| `CNX-E199` | Internal | Unexpected internal failure | Preserve the diagnostic log and report the code and full Blender error. |

## Diagnostic locations and persistence

- Full Bake failures: Blender configuration folder →
  `cloth_next/logs/bake-errors.log`. This rotating JSON-lines file contains
  the code, job ID, state, activity, stage, summary, and detailed cause.
- Companion lifecycle: the same folder → `companion-startup.log`.
- Per-run solver/worker diagnostics: the `Diagnostic log:` location in the
  Blender error details. The run-local `failure.log` is written atomically.
- Cache metadata remains partial or failed after an unsuccessful Bake. A
  previously complete cache remains authoritative until its replacement is
  fully written and validated.

When reporting a problem, include the exact code, its `bake-errors.log` entry,
and the per-run `failure.log` when available. These files stay local; Cloth
NeXt does not automatically submit them or require a PolyWorks account.
