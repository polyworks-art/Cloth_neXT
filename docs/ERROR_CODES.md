# Cloth NeXt Bake error codes

The Bake companion deliberately shows only a stable `CNX-E…` code in its
activity bar. It stays open and pulses red until the user closes it. Full local
details are retained in Blender and in the rotating `bake-errors.log` described
below. The broad `x00` code in each group is the compatibility fallback when a
more specific cause cannot be proven.

## Scene validation (`CNX-E10x`)

| Code | Cause | First action |
| --- | --- | --- |
| `CNX-E100` | Unclassified scene validation failure | Correct the highlighted scene setting. |
| `CNX-E101` | No enabled deformable | Enable Cloth NeXt on a supported Mesh or Curve. |
| `CNX-E102` | Invalid/inconsistent Bake range | Use one valid common range. |
| `CNX-E103` | Unsupported/changing topology | Keep evaluated topology constant. |
| `CNX-E104` | Invalid material or quality value | Correct the highlighted numeric value. |
| `CNX-E105` | Invalid Pin group/animation | Check the group, mode, and animated topology. |
| `CNX-E106` | Incompatible multi-object settings | Align ranges and supported object settings. |
| `CNX-E107` | Object disappeared during preparation | Restore it and do not delete it during startup. |
| `CNX-E108` | Invalid Force configuration | Use a supported Force on an Empty. |
| `CNX-E109` | Non-finite/malformed geometry | Repair NaN/Inf coordinates, transforms, and degeneracy. |

## Companion startup (`CNX-E11x`)

| Code | Cause | First action |
| --- | --- | --- |
| `CNX-E110` | Unclassified Companion startup failure | Close stale Bake windows and retry. |
| `CNX-E111` | Bundle missing/integrity failure | Repair or reinstall Cloth NeXt. |
| `CNX-E112` | Local transport startup failure | Check stale windows and local security software. |
| `CNX-E113` | Companion process launch failure | Check Windows execution permissions. |
| `CNX-E114` | Readiness handshake timeout | Close the existing Bake window and retry. |
| `CNX-E115` | Window not visible/topmost | Restore normal desktop/window access. |
| `CNX-E116` | Authentication/protocol mismatch | Close all Bake windows and reinstall the matching build. |

## Bake preparation (`CNX-E12x`)

| Code | Cause | First action |
| --- | --- | --- |
| `CNX-E120` | Unclassified preparation/export failure | Check evaluated geometry and cache access. |
| `CNX-E121` | Work/cache directory not writable | Choose a writable cache location. |
| `CNX-E122` | Evaluated geometry export failed | Check modifiers and evaluated geometry. |
| `CNX-E123` | Scene/parameter encoding failed | Check finite geometry, materials, Pins, and Forces. |
| `CNX-E124` | Animated Pin capture failed | Keep pinned topology unchanged over the range. |
| `CNX-E125` | Insufficient disk space | Free space or move the cache. |
| `CNX-E126` | Bake worker could not start | Finish other heavy work and retry. |
| `CNX-E127` | Interrupted/stale partial Bake | Clear the partial result or Rebake. |

## Solver startup (`CNX-E13x`)

| Code | Cause | First action |
| --- | --- | --- |
| `CNX-E130` | Unclassified solver startup failure | Run the solver health check. |
| `CNX-E131` | Executable/runtime dependency missing | Repair the managed solver. |
| `CNX-E132` | Protocol/schema/package incompatible | Install the solver required by this release. |
| `CNX-E133` | Solver health check failed | Repair the solver and inspect startup diagnostics. |
| `CNX-E134` | Solver exited during startup | Inspect stderr; verify drivers/dependencies. |
| `CNX-E135` | Execution permission denied | Allow the solver executable. |

## Scene upload (`CNX-E14x`)

| Code | Cause | First action |
| --- | --- | --- |
| `CNX-E140` | Unclassified upload failure | Check the local solver connection. |
| `CNX-E141` | Connection/response timeout | Check localhost security software. |
| `CNX-E142` | Connection closed/broke | Retry after a successful health check. |
| `CNX-E143` | Upload rejected/not acknowledged | Verify the matching solver version. |
| `CNX-E144` | Payload hash/identity mismatch | Repair the solver and start a fresh Bake. |
| `CNX-E145` | Malformed/oversized response | Repair or update the solver. |

## Project build (`CNX-E15x`)

| Code | Cause | First action |
| --- | --- | --- |
| `CNX-E150` | Unclassified project-build failure | Inspect geometry and the solver log. |
| `CNX-E151` | Solver rejected the build | Check geometry, materials, Pins, and Forces. |
| `CNX-E152` | Build timed out | Simplify the scene and retry. |
| `CNX-E153` | Contact/geometry initialization failed | Repair intersections and degenerate collision geometry. |
| `CNX-E154` | Project unexpectedly busy | Wait for owned solver work to stop. |

## Simulation (`CNX-E16x`)

| Code | Cause | First action |
| --- | --- | --- |
| `CNX-E160` | Unclassified simulation failure | Inspect the failing frame and diagnostic log. |
| `CNX-E161` | Constraints did not converge | Reduce Time Step/extreme stiffness; inspect collision geometry. |
| `CNX-E162` | Initial intersection | Separate intersecting geometry on the first frame. |
| `CNX-E163` | Simulation stalled/timed out | Inspect the last frame and reduce complexity. |
| `CNX-E164` | Solver crashed/exited | Inspect stderr and GPU/driver stability. |
| `CNX-E165` | Non-finite result | Reduce Time Step and extreme Forces/stiffness. |
| `CNX-E166` | RAM safety limit reached | Lower complexity or cautiously raise Auto Cancel RAM. |
| `CNX-E167` | Requested frames incomplete | Retry after a health check and retain diagnostics. |
| `CNX-E168` | Force/parameter instability | Reduce animated Force magnitude/extreme parameters. |
| `CNX-E169` | Unexpected solver state | Stop other solver work and retry fresh. |

## Result transfer (`CNX-E17x`)

| Code | Cause | First action |
| --- | --- | --- |
| `CNX-E170` | Unclassified transfer failure | Check disk space and localhost connection. |
| `CNX-E171` | Transfer timed out | Check disk performance and local security software. |
| `CNX-E172` | Connection broke mid-transfer | Retry after a health check. |
| `CNX-E173` | Invalid/missing output map | Repair/update the solver and retain diagnostics. |
| `CNX-E174` | Result frame missing | Retry and report if the same frame repeats. |
| `CNX-E175` | Result frame corrupt/undecodable | Check disk integrity and solver installation. |
| `CNX-E176` | Result size/vertex mismatch | Restore the original topology and Rebake. |

## Playback cache (`CNX-E18x`)

| Code | Cause | First action |
| --- | --- | --- |
| `CNX-E180` | Unclassified playback-cache failure | Check cache access and target topology. |
| `CNX-E181` | Cache write failed | Choose a writable folder with enough space. |
| `CNX-E182` | PC2 finalization failed | Check disk/filesystem reliability. |
| `CNX-E183` | Topology changed before import | Restore the topology used at Bake start. |
| `CNX-E184` | Target object disappeared | Restore it and Rebake. |
| `CNX-E185` | Playback attachment failed | Remove conflicting cache modifiers/animation. |
| `CNX-E186` | Cache integrity failed | Discard the partial cache and Rebake. |
| `CNX-E187` | Multi-object cache set inconsistent | Keep all targets unchanged and Rebake together. |
| `CNX-E188` | Rod Curve playback failed | Restore Curve topology and remove conflicting animation. |

## Cleanup/internal (`CNX-E19x`)

| Code | Cause | First action |
| --- | --- | --- |
| `CNX-E190` | Unclassified cancellation cleanup failure | Wait; restart Blender only if it remains stuck. |
| `CNX-E191` | Cancellation timeout | Wait briefly, then restart if the owned solver remains active. |
| `CNX-E192` | Owned solver could not stop | Preserve diagnostics and close Blender. |
| `CNX-E193` | Partial/temp files could not be removed | Close processes using the cache folder. |
| `CNX-E198` | Worker died without terminal result | Preserve diagnostics and restart Blender. |
| `CNX-E199` | Unexpected internal failure | Report the code with the full diagnostic record. |

## Diagnostic locations and persistence

- Full Bake failures: Blender configuration folder →
  `cloth_next/logs/bake-errors.log`. This JSON-lines file contains the code,
  job ID, state, activity, stage, summary, and detailed cause. It rotates at
  1 MiB to `bake-errors.log.1` and is flushed to disk before the UI continues.
- Companion lifecycle: the same folder → `companion-startup.log`.
- Per-run solver/worker traceback: the `Diagnostic log:` path recorded in the
  Blender error details. `failure.log` is published with atomic replacement.
- Cache metadata remains `PARTIAL`/`failed` after failure and is never presented
  as a complete result. A previously complete cache is preserved until a new
  Bake passes validation and the Companion readiness gate.

When filing a bug, include the exact code, the matching JSON line from
`bake-errors.log`, and the per-run `failure.log` if present. These files stay
local; Cloth NeXt does not upload diagnostics automatically.
