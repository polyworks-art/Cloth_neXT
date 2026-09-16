# Installation presence implementation report

## Architecture and lifecycle

The extension uses `blender/registration.py` for ordered, reversible registration,
`blender/preferences.py` for Add-on Preferences, and Blender timers with daemon
workers for existing background operations. The Companion is optional and lives
for bake or information windows; it is not a continuously running service.
Therefore the add-on exclusively owns presence scheduling and transport. No
Companion or PPF solver changes were made.

`blender/presence_runtime.py` registers one persistent Blender timer, first due
after two seconds. A single service submits at most one outstanding daemon HTTPS
request. Main-thread code reads configuration and copies the four payload values;
the worker imports no `bpy` and receives only the copied dictionary. No network
work happens on the Blender UI thread. Timer checks do not send on frame,
depsgraph, redraw, object, or simulation events.

Requests are approximately 120 seconds apart. An accepted username edit makes
the next timer tick due. Channel changes are reflected on the next normal send.
Unregister removes current and stale timers and stops future submissions without
waiting on networking. An in-flight bounded daemon request may finish afterward;
the retained service prevents overlap if the add-on is registered again or its
runtime module is reloaded. There is no response handler or functional state
mutation associated with the worker.

## Preferences and persistence

The existing General preferences section contains `Superhive Username`. Its
getter and setter use `IdentityStore`; values are trimmed, empty values and
control characters are rejected, and the maximum normalized length is 128.
Unknown usernames and names containing spaces are accepted. Invalid edits leave
the last accepted value intact. When no name exists, the timer requests a simple
Blender username dialog with Continue. Cancelling does not restrict functionality;
the field remains available in Preferences. No runtime network-error dialog or
opt-in toggle was added.

Both username and random UUID4 Installation ID are authoritative in
`cloth_next/presence.json` under Blender's user configuration directory. This
uses Blender's configuration location and is separate from project files and
solver storage. UUID4 is created once and preserved across restarts and add-on
updates using this durable copy.

At the user's request, a replaceable installed copy is also maintained under
`resources/.state/r7.dat` inside the add-on. Deleting that installed copy restores
it from configuration at the next normal check. Read-only add-on directories are
tolerated. Neutral naming is obscurity, not tamper-proof enforcement; removing
all copies can reset the identity. Both builders exclude installation-specific
state from packages, and Git ignores the installed copy.

## Protocol and privacy

POST only to `https://tinytrouble.de/cloth-next/api/heartbeat.php`, with
`Content-Type: application/json` and exactly:

```json
{
  "superhive_username": "<trimmed configured username>",
  "install_id": "<persistent random UUID4>",
  "version": "<manifest_version()>",
  "channel": "<existing update_channel, lowercased>"
}
```

The version comes from the canonical `blender_manifest.toml` through the existing
`manifest_version()` function. The existing selected Stable/Beta/Dev preference
provides channel state, with its existing default as fallback. The official
version was preserved at 2.4.9 for the initial implementation, then updated to
2.4.10 for the separately authorized Dev release. No second version definition
or channel preference exists.

Default TLS certificate validation stays enabled. Redirects are refused, so
there is no alternate endpoint or HTTP fallback. Timeout is four seconds.
HTTP 204 completes normally; other responses and all network exceptions are
discarded without reading response bodies or deriving entitlement information.
HTTP error response resources are closed. Failures produce no user notification,
retry storm, purchase status, simulation change, or startup blocking. Normal
interval scheduling continues. Shutdown does not wait for an in-flight request.

The server observes source IP naturally; IP is not a payload field. No hardware
fingerprint, admin secret, credentials, purchaser list, purchase enforcement,
Superhive lookup, project/scene data, or unrelated telemetry was added. Any
purchaser comparison belongs exclusively to the owner's separate system. Privacy
documentation is in `docs/PRIVACY.md`; extension permission descriptions were
updated to accurately cover presence networking and persistence.

## Changed files

- `cloth_next/presence.py`: identity persistence, payload, silent HTTPS worker.
- `cloth_next/blender/presence_runtime.py`: timer, Preferences callbacks, first-run dialog.
- `cloth_next/blender/preferences.py`: existing Preferences field.
- `cloth_next/blender/registration.py`: reversible lifecycle integration.
- `cloth_next/blender_manifest.toml`: permission wording and build exclusions; version unchanged.
- `tools/build_extension.py`: exclude installation-specific runtime state.
- `.gitignore`: ignore installed runtime copy.
- `tests/test_presence.py`: identity, protocol, worker, and lifecycle tests.
- `tests/fake_bpy.py`: support Blender's persistent timer registration argument.
- `tests/test_extension_build.py`: runtime-state exclusion coverage.
- `docs/PRIVACY.md` and this report: transparent documentation.

## Validation

All presence-network tests mock transport. The real Blender smoke script also
replaces transport and uses temporary identity storage; no validation heartbeat
contacts the server.

New automated coverage includes username persistence/normalization/rejection/
length, UUID creation/persistence/randomness, installed-copy restoration, exact
payload keys, canonical version and existing channel, HTTP 204, timeout, DNS,
TLS, connection reset and HTTP errors, silence, scheduler interval behavior,
username changes, duplicate prevention, cleanup, reload, first-run prompting,
worker shutdown, unchanged functional state, and forbidden identifier checks.

- Full pytest run: **1,772 passed, 10 skipped, 3 deselected, 1 failed**.
  The failing `test_extension_source_root` rejects pre-existing staged
  `bin/cloth-next-bake.exe` and `companion_manifest.json` in this checkout.
  Source-phase validation has the same existing layout failure. These generated
  artifacts were not removed or rebuilt for this task.
- Final focused presence, hardening, and extension-build checks: **39 passed**,
  including the first-run test added after the full run began.
- Real Blender 5.2.2 register/unregister/reload smoke: **passed**.
- Compile checks for extension, Companion source, and tools: **passed**.
- Ruff for new modules/tests and touched build modules: **passed**.
- `git diff --check`: **passed**.
- Local Python and official Blender extension builds: **passed**.
- Final official-tooling ZIP validation and solver-artifact scan: **passed**.
- Complete ZIP byte scan for MachineGuid, Win32_BaseBoard, wmic, motherboard,
  serialnumber, MAC address, CLOTH_NEXT_ADMIN_API_KEY, and X-API-Key: **no hits**.
- ZIP contains no runtime identity copies or compiled Python bytecode.
- No configured type checker was found. PPF integration checks requiring an
  external solver were skipped; no solver was modified or bundled.

The final official-tooling artifact is
`dist/cloth-next-presence-blender-local.zip`. It reuses the checkout's already
staged Companion. No tag, release, deployment, publication, or server-side
purchaser integration was created.
