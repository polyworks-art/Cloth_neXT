# PPF health check

## Verified query

Compatibility and status use the same side-effect-free project ping in supported PPF releases. For
project `demo`, the exact request is:

```text
54 43 4d 44 00 00 00 0b 2d 2d 6e 61 6d 65 20 64 65 6d 6f
 T  C  M  D  [u32 BE: 11]       --name demo
```

The response is one UTF-8 JSON document followed by newline and EOF. Cloth NeXt uses
separate connect/read timeouts, partial-read accumulation, a 1 MiB default response cap
(hard maximum 16 MiB), and a socket context manager.

## Runtime status ownership and reconnect policy

One production Bake has exactly one status owner: the `SolverSession` worker.
Its BUILD and SIMULATE loops call the same synchronous `_status()` method; the
next poll cannot begin until the previous socket context has closed. The UI and
Companion consume `SessionEvent`/`BakeSnapshot` data and never contact PPF.
Cancellation uses the same session worker after the polling loop unwinds. The
startup health runner polls only before `SolverSession` enters upload/build, and
the preferences health runner is a separate explicitly requested installation
test protected by the process-wide workflow reservation. Recovery and frame
fetching also run serially in the session worker. There is therefore no second
timer, diagnostics thread, or independent health loop sharing a Bake server.

Every status poll opens one short-lived TCP connection. The production session
uses a 5 second connect timeout and 30 second read timeout; the 2/2 second
`TransportConfig` defaults remain for standalone probes and tests. Failures are
classified as `CONNECT_TIMEOUT`, `READ_TIMEOUT`, `CONNECTION_REFUSED`,
`CONNECTION_RESET`, `CONNECT_ERROR`, `READ_ERROR`, `SEND_ERROR`, or
`INVALID_RESPONSE`.

During BUILDING or SIMULATING only, timeout/reset/refusal failures enter a
bounded reconnect state. At most three retries use 0.25, 1, and 2 second delays
(scaled from the configured polling interval and capped at 2 seconds), and all
attempts must remain within a 60 second grace window. A successful status resets
the failure streak. Cancellation interrupts backoff immediately. A known-owned
process exit bypasses retries. `build`, `start`, `resume`, `terminate`, and all
other lifecycle commands are outside this policy, so a reconnect never resends
`start` or creates a second simulation.

Metrics are bounded counters/scalars: request/success/failure counts,
consecutive failures, current/max latency, failure phase, and age of the last
valid status. Persistent loss still maps to CNX-E140 with owned process/job and
log-tail evidence.

Always-present successful fields are `status`, `data`, `frame`, `initialized`, `error`,
`violations`, `root`, `upload_id`, `data_hash`, `param_hash`, `protocol_version`,
`hardware`, and `git_branch`. Protocol 0.18 also supplies optional structured
`crash_kind` diagnostics. TCMD errors include `NO_ID` and text-decode failures.

The response exposes no `schema_version` and no package version. The local executable's
verified `--version` output is:

```text
ppf-cts-server 0.1.0 (protocol v0.18, schema v2)
```

An owned local solver can therefore be fully validated. An external server can be
identified as PPF and protocol-compatible, but schema/package remain unknown; Cloth
NeXt reports it as not fully verified instead of inventing fields.

## Wire-state hints

| PPF wire status | Application hint |
|---|---|
| `NO_DATA`, `NO_BUILD` | none; capability/status only |
| `BUILDING` | `STARTING` |
| `READY` | `READY` |
| `RESUMABLE` | `PAUSED` |
| `FAILED` | `ERROR` |
| `BUSY` | `SIMULATING` |
| `SAVE_AND_QUIT` | `CANCELLING` |

Hints do not transition the application state machine in Phase 2.

## Manual command and real test

```powershell
python tools\ppf_health_check.py --executable "C:\Path\ppf-cts-server.exe" --host 127.0.0.1 --port 9090
```

For the real test, set `CLOTH_NEXT_PPF_EXECUTABLE` to a pinned binary and ensure port
19090 is free:

```powershell
$env:CLOTH_NEXT_PPF_EXECUTABLE = 'C:\path\ppf-cts-server.exe'
python -m pytest tests\integration\test_real_ppf_health.py -m integration -v
```

No solver is downloaded. Without the variable, the test is intentionally skipped.

The test uses an ephemeral port and temporary runtime directory and can run against a
local official Windows installation selected with the environment variable. Extension
and repository directories are not searched for bundled solvers.
