# Local PPF process lifecycle

For Rebake, Cloth NeXt identifies playback through a Mesh Cache type plus an ownership marker and matching recorded cache path (with result-metadata migration for older owned caches). It disables viewport/render evaluation only while capturing, restores both flags in `finally`, and retains the old cache until scene validation, animated target capture, and companion readiness have succeeded. Only then does the existing object-scoped replacement service remove the prior owned result and start PPF.

The mist Canvas is constructed before readiness, but animation success is not part of the matching-job, visibility, mapping, topmost, or transport-ready contract. Its one `after()` chain starts once and is cancelled idempotently before `root.destroy`, preserving polling, Cancel, disconnect, and terminal auto-close behavior.

The optional owned companion is started or reused by Bake. Terminal snapshots
request graceful close after 1.5 seconds (Finished), 1 second (Cancelled), or
2.5 seconds (Error); only that owned process is terminated after the additional
bounded timeout. A crash during simulation warns `Bake window closed
unexpectedly.` while Blender-side progress and Cancel remain operational.

`Popen` and process liveness are not readiness. The companion must acknowledge
the exact job after Tk has deiconified, mapped, applied topmost, lifted, and
performed one bounded focus request. Missing executable, invalid manifest,
transport failure, process exit, hidden window, failed topmost, Cancel, or
timeout abort startup before any worker/PPF process or cache replacement. Logs
rotate under the user's Cloth NeXt configuration/runtime log directory and the
companion's Local AppData log directory, never inside the extension.

Baseline: upstream `7193f158e3843597070f66cb29af19efd9bdcff7`, protocol
`0.11`, schema `1`, package `0.1.0`.

```text
validate executable -> probe port
                         | occupied -> valid TCMD: EXTERNAL
                         |          -> invalid: PORT CONFLICT
                         v free (advisory only)
run executable --version -> Popen argument list, shell=False
                         -> bounded stdout/stderr readers
                         -> poll child and unique absolute progress file
                         -> SERVER_READY marker AND compatible TCMD status
                         -> OWNED ready
                         -> terminate -> timed wait -> kill fallback
                         -> wait/reap -> join readers
```

`SERVER_STARTING` and `SERVER_READY` are the only verified progress markers. Files are
unique under the OS temporary directory (`cloth-next/progress-<uuid>.log`), never the
repository. Missing files are normal during startup; reads and retained tails are
bounded.

## Ownership and shutdown

`OWNED_PROCESS` is created only after Cloth NeXt calls `Popen`. It may be polled,
terminated, killed after a shutdown timeout, reaped, and restarted. `EXTERNAL_SERVER`
holds no child handle, PID, progress-file ownership, or shutdown authority. Calls to
start, stop, or restart through an external-mode manager raise `PermissionError`.

The audited server has no separate verified administrative server-shutdown request for
the pre-simulation health state. The owned child therefore uses `Popen.terminate()`,
waits, then uses `kill()` only after timeout. It never uses `taskkill` or a shell.
Reader threads are non-daemon, close streams, and must join before cleanup returns.

## Readiness, races, and failure

The telemetry worker starts with HUD registration, samples into immutable cache
at a bounded interval, and is stopped and joined during HUD unregister. The
owned solver PID is metadata only and is cleared on every terminal path. The
optional Bake companion is launched only by an explicit real run, reused if
already open, and remains open after terminal states for inspection. Closing it
does not imply cancel; unregister shuts down only the exact owned companion.

For a Phase-3A owned solve, Cloth NeXt pins `PPF_CTS_DATA_ROOT` below the run
work directory, uses a unique `clothnext_<12hex>` project, and deletes only
that project before stopping and reaping only its owned child. Cancellation
uses `cancel_build` during BUILDING and `terminate` during simulation. An
external server receives the scoped project lifecycle requests but is never
stopped or killed by Cloth NeXt.

A free preflight port is advisory; another process can bind before the child. The
manager therefore continues checking child exit and the real compatibility response.
A reachable port is not called PPF until valid status JSON with `protocol_version` and
a known wire status parses. Invalid UTF-8/JSON, missing fields, wrong protocol, and
timeouts are categorized. An early exit records code plus bounded stderr/progress tails.

Owned startup requires both the marker and successful compatible query. External
servers do not require or manipulate a progress file.

Owned local deployments may supply solver-tree-specific `PATH` and `PYTHONPATH`
entries to the child process. Progress lives in `%TEMP%/ClothNeXt*`; neither repository
nor extension directories receive mutable runtime files. The Phase 2.5 prototype
exercised this lifecycle against the real official Windows binary and verified no
server process remained; current packages do not bundle that runtime.

## Result streaming and cancellation

The worker opens a transactional PC2 writer before simulation and writes the
captured initial pose. Each complete solver frame is transferred, decoded,
extracted, transformed, and written immediately. Queue events expose per-frame
`Creating playback cache` progress and a distinct `Finalizing playback cache`
phase; only the main-thread pump changes Blender UI or modifiers. Cancel checks
bracket transformation and writing. All failure, cancel, and shutdown paths
abort the temporary writer, publish no sidecar, create no modifier, and leave a
previous valid cache untouched. Modifier attach remains a short main-thread
operation after final cache validation.

## Add-on update handoff

Before the "Update through Blender" handoff, Cloth NeXt stops only the PPF
solver processes it started itself (never an external server), quiesces UI
preview jobs, and shuts down only the owned Bake companion. It then
synchronizes the exact selected channel repository and opens Blender's
native extension update view. The actual package replacement — including
disabling and re-enabling the extension — happens exclusively inside
Blender's own extension manager after the Cloth NeXt operator has returned.
Cloth NeXt never installs, replaces, reloads, or unregisters its own running
package from its own Python stack: doing so is a native module-lifetime
hazard that can crash Blender and cannot be made safe with try/except or a
deferred timer. Update checks and package installation are therefore two
separate lifecycles. Caches, PC2 modifiers, and scene data are never touched
by the handoff.
