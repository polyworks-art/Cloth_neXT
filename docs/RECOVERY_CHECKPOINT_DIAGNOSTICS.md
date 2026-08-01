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

For a repeatable managed-solver run, provide the Blender executable and the
selected solver explicitly:

```powershell
$blender = 'C:\Path\To\blender.exe'
$solver = 'C:\Path\To\ppf-cts-server.exe'
$root = (Get-Location).Path
$work = Join-Path $env:TEMP 'cloth-next-recovery-proof'
& $blender --background --python tools\blender_recovery_integration.py -- `
  --phase cancel --repo $root --blend "$work\recovery.blend" `
  --cache "$work\cache" --report "$work\cancel.json" --solver $solver
```

Run the same scene with the supported Schema 1 and Schema 2 solver selections.
The generated JSON captures the persisted recovery result; the structured Bake
log supplies the per-poll status and file evidence. This is an integration
procedure, not a mocked proof.
