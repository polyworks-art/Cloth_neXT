# PPF health check

## Verified query

Compatibility and status are the same side-effect-free project ping in PPF 0.11. For
project `demo`, the exact request is:

```text
54 43 4d 44 00 00 00 0b 2d 2d 6e 61 6d 65 20 64 65 6d 6f
 T  C  M  D  [u32 BE: 11]       --name demo
```

The response is one UTF-8 JSON document followed by newline and EOF. Cloth NeXt uses
separate connect/read timeouts, partial-read accumulation, a 1 MiB default response cap
(hard maximum 16 MiB), and a socket context manager.

Always-present successful fields are `status`, `data`, `frame`, `initialized`, `error`,
`violations`, `root`, `upload_id`, `data_hash`, `param_hash`, `protocol_version`,
`hardware`, and `git_branch`. TCMD errors include `NO_ID` and text-decode failures.

The response exposes no `schema_version` and no package version. The local executable's
verified `--version` output is:

```text
ppf-cts-server 0.1.0 (protocol v0.11, schema v1)
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

Since Phase 2.5 the test also resolves extension- and repository-bundled installations,
uses an ephemeral port and temporary runtime directory, and passes against the local
official Windows bundle. The environment variable retains highest priority.
