# Cloth NeXt Bake error codes

The Bake companion intentionally displays only a short error code. Blender's
Cloth NeXt panel and diagnostic logs retain the complete error summary, stage,
cause, and recommended action.

| Code | Stage | First checks |
| --- | --- | --- |
| `CNX-E100` | Scene validation | Check enabled deformables, Bake range, topology, materials, and Pin groups. |
| `CNX-E110` | Companion startup | Close stale Bake windows and retry; inspect `companion-startup.log`. |
| `CNX-E120` | Bake preparation/export | Check evaluated geometry, modifiers, cache folder access, and animated inputs. |
| `CNX-E130` | PPF startup | Run the solver health check and repair or reselect the managed solver. |
| `CNX-E140` | Scene upload | Check the local solver connection and retry. |
| `CNX-E150` | PPF project build | Check geometry validity and the solver diagnostic log. |
| `CNX-E160` | Simulation | Inspect the failing frame, forces, materials, Pins, and stability settings. |
| `CNX-E170` | Result transfer | Check disk space, the local solver connection, and solver output. |
| `CNX-E180` | Playback cache import | Check cache-folder access and the target object's topology. |
| `CNX-E190` | Cancellation cleanup | Wait for owned-process cleanup; restart Blender only if it remains stuck. |
| `CNX-E199` | Internal/unclassified | Preserve the Blender diagnostic log and report the exact code and full error text. |

## Where to find full details

- Blender: Physics Properties → Cloth NeXt → Cache/Bake status.
- Companion startup: Blender configuration folder →
  `cloth_next/logs/companion-startup.log`.
- Solver/Bake diagnostics: use the log path shown by the Blender error details.

The code identifies the stage, not one unique root cause. Always use the full
Blender message when filing a bug report.
