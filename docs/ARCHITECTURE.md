# Cloth NeXt architecture audit

Status: audit baseline, 2026-07-12. No implementation is authorized by this document.

## Repository inventory

At audit start the complete tracked repository consists of:

| Path | Classification | Decision |
|---|---|---|
| `README.md` | name/one-line product intent | retain only the visible name; rewrite later |
| `LICENSE` | neutral legal asset (GPL-3.0 text) | retain pending an explicit licensing decision |

There are no packages, hidden source modules, binaries, images, tests, solver code,
UI code, cache code, manifests, or old architecture in the repository. Consequently:

- old solver/UI/cache files marked for removal: **none**;
- branding-only assets: the text name “Cloth NeXt” in `README.md`;
- neutral assets: `LICENSE`;
- reusable old implementation: **none**.

Git status was clean; history contains only the initial commit. The official PPF
checkout used for research was kept outside this repository and no code was copied.

## Blender extension root

The repository contains the extension source directory `cloth_next/`. That directory,
not the repository root and not a nested second `cloth_next/`, is passed to Blender's
extension builder. Its `blender_manifest.toml` and registration `__init__.py` are at the
archive root:

```text
cloth_next-0.1.0.zip
  blender_manifest.toml
  __init__.py
  blender/
  core/
  ppf/
  ...
```

This follows Blender's documented extension layout. `tools/validate_extension.py`
rejects missing root files and redundant nesting; `tests/test_package_structure.py`
builds and validates a representative ZIP. All internal imports are relative. The
top-level entry point imports `bpy` lazily through `blender/registration.py`, allowing
pure submodules to import in normal Python.

## Architectural boundary

Phase 2.8B adds a Blender-free, thread-safe bake controller whose immutable
snapshots are shared by Physics panels, the Viewport HUD and optional companion
transport. The companion is separately owned from every PPF process; its future
IPC boundary is authenticated, bounded JSON on localhost only. See
`UI_ARCHITECTURE.md`.

Cloth NeXt is a Blender client and pipeline for PPF, never a physics engine.

```text
Blender UI/properties
        |
application services + explicit state machine
        |
SimulationBackend interface
        |
cloth_next/ppf (the only protocol-aware package)
        |
external ppf-cts-server / PPF solver
```

The proposed package layout is the layout requested in the product brief. Create it
only in the Ground Structure phase:

```text
cloth_next/
  __init__.py
  blender_manifest.toml
  core/ properties/ ui/ ppf/ simulation/ updater/ hud/ tests/
```

Rules:

- `ui` depends on application-facing view models, never wire details.
- `ppf` owns transport, CBOR encoding/decoding, compatibility checks and process
  launching. No runtime import from the official add-on is permitted.
- pure dataclasses carry scene/job/cache data; Blender objects do not cross worker
  boundaries.
- worker threads perform blocking process/file/socket work and post immutable results
  to queues. `bpy` access and datablock mutation occur only on Blender's main thread,
  scheduled with `bpy.app.timers`.
- caches are owned by `simulation`, and store provenance plus stable constant-topology
  playback data. PC2 is the first candidate because the official add-on proves Blender
  Mesh Cache playback and appendable frames; it still requires a Cloth NeXt metadata
  sidecar and crash-safe publishing.
- updater and HUD consume interfaces and state snapshots; neither controls simulation.

## Backend contract direction

Do not freeze the illustrative interface from the brief verbatim. The verified server
is project-oriented, not job-ID-oriented. The first interface should model:

- compatibility/status query;
- atomic scene/parameter transfer;
- build/rebuild;
- run, terminate, and save-and-quit;
- fetch output map and available complete frames;
- fetch a specific frame;
- project cleanup.

Opaque `ProjectId`, `UploadId`, protocol version and schema version types should make
cross-project and incompatible-server mistakes difficult. Exact request models remain
inside `cloth_next/ppf`.

## Solver state machine

The product states are retained: `NOT_INSTALLED`, `INSTALLING`, `STOPPED`, `STARTING`,
`READY`, `TRANSFERRING`, `SIMULATING`, `PAUSED`, `FETCHING_FRAMES`, `CANCELLING`,
`UPDATING`, `ERROR`. These are Cloth NeXt application states, not aliases for PPF's
wire statuses (`NO_DATA`, `NO_BUILD`, `BUILDING`, `READY`, `RESUMABLE`, `FAILED`,
`BUSY`, `SAVE_AND_QUIT`). A dedicated mapper translates verified server responses.

All transitions are pure and table-driven. Side effects are emitted as commands and
executed by injected services. Invalid transitions return a categorized error and log
context. UI enablement derives from state/capabilities, never ad-hoc panel logic.

`PAUSED` has one narrow meaning: the solver has produced a demonstrably resumable saved
state. Only `RESUMABLE_STATE_SAVED` can enter it. It never means suspending an arbitrary
running process. PPF wire-state mapping remains a future adapter at the `ppf` boundary.

### Phase 1 transition table

This table is implemented by the immutable `_RULES` mapping in `core/state.py`; the
test suite parameterizes directly over that mapping so every declared row is exercised.

| From | Event | To | Command, if any |
|---|---|---|---|
| NOT_INSTALLED | INSTALL_REQUESTED | INSTALLING | INSTALL_SOLVER |
| INSTALLING | INSTALL_SUCCEEDED / INSTALL_FAILED | STOPPED / ERROR | — |
| STOPPED | START_REQUESTED | STARTING | START_BACKEND |
| STARTING | START_SUCCEEDED / START_FAILED | READY / ERROR | — |
| READY | TRANSFER_REQUESTED | TRANSFERRING | TRANSFER_SCENE |
| TRANSFERRING | TRANSFER_SUCCEEDED / TRANSFER_FAILED | READY / ERROR | — |
| READY or PAUSED | SIMULATION_REQUESTED | SIMULATING | START_SIMULATION |
| SIMULATING | RESUMABLE_STATE_SAVED | PAUSED | — |
| READY, PAUSED or SIMULATING | FETCH_REQUESTED | FETCHING_FRAMES | FETCH_FRAMES |
| FETCHING_FRAMES | FETCH_COMPLETED | READY | — |
| SIMULATING, TRANSFERRING or FETCHING_FRAMES | CANCEL_REQUESTED | CANCELLING | CANCEL_OPERATION |
| CANCELLING | CANCEL_COMPLETED | READY | — |
| READY or PAUSED | STOPPED | STOPPED | — |
| STOPPED | UPDATE_REQUESTED | UPDATING | APPLY_UPDATE |
| UPDATING | UPDATE_COMPLETED | STOPPED | — |
| any non-ERROR state | OPERATION_FAILED | ERROR | — |
| ERROR | RECOVER_TO_STOPPED / RECOVER_TO_READY | STOPPED / READY | — |

Any other pair is rejected without advancing the state or revision and returns an
`INTERNAL`, recoverable `ErrorRecord`. Failure events may carry a more specific typed
error record from the operation boundary.

## Phase 1 dependency direction

```text
extension __init__ -> blender.registration -> bpy
                 \-> core (pure Python)
                 \-> ppf contracts/models (pure Python)
ui/properties/simulation/updater/hud -> placeholders only
```

Pure modules may use the standard library and other pure Cloth NeXt modules, but cannot
import `bpy` or Blender adapters. `ppf/contracts.py` expresses operations without
socket, framing, subprocess or CBOR implementation. See `DEPENDENCIES.md` for the wheel
and optional-feature policy.

## Phase 2 runtime boundary

`ppf/transport.py` implements only bounded TCP TCMD status pings.
`compatibility.py` owns the pinned version policy, `status.py` parses wire states,
`progress.py` reads bounded marker tails, `process.py` owns locally spawned children,
and `health.py` composes immutable results. All are Blender-free. Process reader
threads communicate through `queue.Queue`; no callback or message contains Blender
data. See `PROCESS_LIFECYCLE.md`.

## Phase 2.5 solver deployment

`ppf/layout.py` describes an immutable complete runtime tree. `ppf/resolver.py` applies
the fixed priority external installation, extension bundle, repository bundle, then
external server. Extension bundles are always read-only; repository writes are confined
to the explicit bootstrap tool. `ppf/bootstrap.py` provides secure staging and atomic
exchange primitives. All mutable process state is injected under the OS temporary root.

The extension root is derived from `ppf/resolver.py`'s module path. A repository root is
accepted only when both `pyproject.toml` and `cloth_next/blender_manifest.toml` exist;
there is no open-ended parent search or developer-specific absolute path.

## Sources

Research baseline: official repository commit
[`7193f158`](https://github.com/st-tech/ppf-contact-solver/tree/7193f158e3843597070f66cb29af19efd9bdcff7),
especially the [server](https://github.com/st-tech/ppf-contact-solver/tree/7193f158e3843597070f66cb29af19efd9bdcff7/crates/ppf-cts-server)
and [official Blender add-on](https://github.com/st-tech/ppf-contact-solver/tree/7193f158e3843597070f66cb29af19efd9bdcff7/blender_addon).

## Release and update architecture (Phase 2.6)

Version source of truth is `cloth_next/blender_manifest.toml`. A pushed tag
`v<version>` triggers `.github/workflows/release.yml`, which validates policy
(`tools/validate_release_policy.py`, three phases), runs all tests, builds the
solver-free extension ZIP through the official Blender tooling, scans it
(`tools/scan_release_artifact.py`), generates `release-manifest.json` and
`SHA256SUMS.txt`, drafts the GitHub release, generates the channel repository
index with `blender --command extension server-generate`, deploys only the
affected channel directory (`stable/` or `beta/`) to the `gh-pages` branch,
and publishes the draft only after every step succeeded.

The solver has a fully separate lifecycle. `cloth_next/updater/` contains the
pure (bpy-free) installer core: `solver_manifest.py` (strict validation of the
metadata-only compatibility manifest), `install_paths.py` (managed layout under
`%LOCALAPPDATA%\ClothNeXt\solver`, outside every extension/repository root),
`download.py` (HTTPS-only, host-restricted, size-limited, cancellable),
`archive.py` (traversal/symlink/reparse/bomb hardening), `managed.py` (the
confirmation-gated pipeline with side-by-side activation and rollback safety),
`external.py` (read-only validation of user-selected installations),
`states.py`/`modes.py`/`view_model.py` (installer state machine, mode
permissions, and the pure preferences presentation model), and
`addon_update_guard.py` (update lockout during active solver work).
`cloth_next/blender/preferences.py` is the only bpy-facing layer and renders
exactly what the view model computes.

## Phase 2.7 hardening notes

The package uses only relative imports internally and therefore works both as
`cloth_next` (repository checkout) and as `bl_ext.<repository>.cloth_next`
(installed Blender extension). Solver resolution knows exactly four sources —
managed installation, user-selected external installation, external server, and
the explicit `CLOTH_NEXT_PPF_EXECUTABLE` development executable; no extension
or repository directory is ever scanned implicitly. `blender_manifest.toml` is
the single version source; `current.json` is strictly validated before use; an
owned solver that fails the compatibility check is stopped and reported as a
protocol error, never as a successful start.
