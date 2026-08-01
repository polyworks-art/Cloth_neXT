# Recovery checkpoint diagnostics

PPF creates a periodic Saved State from the Param payload fields
`scene.auto-save` and `scene.keep-states`; it is not a separate TCMD request.
With interval `N`, the solver saves at displayed output frames `N`, `2N`,
`3N`, and so on. `scene.keep-states` is set from the add-on's retention field,
so solver-side pruning and durable recovery metadata use the same limit. The
files are created on the solver host at:

```text
<server-data-root>/<project>/session/output/state_<solver-frame>.bin.gz
```

Cloth NeXt logs `Recovery checkpoint configuration` at Bake start and
`Recovery checkpoint status observed` for every solver status poll. Those
structured log records include the cadence, retention settings, resolved
output directory, raw `saved_states`, file existence, verified metadata
frames, and whether metadata changed. A cooperative cancel additionally logs
`Recovery checkpoint request sent` for its terminal `save_and_quit` request.

## Real-solver procedure

Use a deliberately long Bake and set **Recovery & Checkpoints** to enabled,
**Auto Save Checkpoints** enabled, interval `2`, retention `3`, and **Save
State on Cancel** enabled. Confirm the following before pressing Cancel:

1. Log entries show `auto_save_interval: 2` and the expected output directory.
2. Status responses report `saved_states` after frames 2, 4, and so on.
3. The output directory contains matching readable `state_<N>.bin.gz` files.
4. `metadata.json` contains a size and SHA-256 only after the file is verified.

Then press **Cancel**. Cloth NeXt sends the sole terminal command
`save_and_quit`, waits for a reported state and verified gzip file, and retains
the newest configured number of records. It never sends `save_and_quit` for a
periodic checkpoint.

## Artist-facing Recovery UI gate

The acceptance gate is `tools/blender_recovery_ui_gate.py`. It deliberately
uses the registered production operators and load handler. It does not set an
internal resume flag, construct a run plan, or invoke a worker directly.

Run its two phases in separate UI-capable Blender processes. The first starts
a real Bake, waits until a checkpoint is durable, saves the `.blend`, and then
hard-aborts Blender with exit code `91`:

```powershell
$blender = 'C:\Path\To\blender.exe'
$solver = 'C:\Path\To\ppf-cts-server.exe'
$root = (Get-Location).Path
$work = Join-Path $env:TEMP 'cloth-next-recovery-proof'
& $blender --factory-startup --python tools\blender_recovery_ui_gate.py -- `
  --phase hard-abort --repo $root --blend "$work\recovery.blend" `
  --cache "$work\cache" --report "$work\evidence.json" --solver $solver

& $blender --factory-startup --python tools\blender_recovery_ui_gate.py -- `
  --phase resume --repo $root --blend "$work\recovery.blend" `
  --cache "$work\cache" --report "$work\evidence.json" --solver $solver
```

The second phase opens the saved file through `bpy.ops.wm.open_mainfile`, waits
for the persistent post-load refresh, requires the primary Simulation panel's
Recovery banner and operator poll to be available, and invokes
`bpy.ops.clothnext.recovery_resume_latest`. The JSON evidence must show the
same project identity, no scene upload or project rebuild, the first resumed
frame after the checkpoint, a solver command containing `--load=-1`, and a
valid final PC2.

`tools/blender_recovery_integration.py` remains useful for lower-level solver
diagnostics, but its direct worker path is not evidence that the artist-facing
Recovery UI works. Run the UI gate with each supported solver schema when
release-level compatibility evidence is required.
