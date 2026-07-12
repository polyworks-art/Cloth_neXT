# PPF 0.11 fixtures

All fixtures target protocol `0.11` at upstream commit
`7193f158e3843597070f66cb29af19efd9bdcff7`. They are deterministic derivations from
the official implementation and its Rust tests, not invented endpoint examples.

| Fixture | Source and derivation | Meaning |
|---|---|---|
| `compatibility_request.bin` | `crates/ppf-cts-server/tests/wire_integration.rs::tcmd_ping_returns_status_response` and `tests/common/mod.rs::send_tcmd`; exact `TCMD` + big-endian length + `--name demo` bytes | Compatibility and status use the same PPF 0.11 ping |
| `status_request.bin` | byte-identical copy of the preceding verified ping | Status request for project `demo` |
| `compatibility_response.json` | `response/shape.rs::base_map`, default `ServerState`, default `EngineConfig`, and `tcmd_ping_returns_status_response` | Complete deterministic default response shape (`NO_DATA`) |
| `status_no_data_response.json` | same source as compatibility response | Default server/project state |
| `status_ready_response.json` | `response/mod.rs::ready_state_response_has_required_fields`; fields expanded through `shape::base_map` defaults | Built, idle project response |
| `executable_version.txt` | `main.rs::version_line`, workspace package version, `PROTOCOL_VERSION`, and `ppf-cts-formats::SCHEMA_VERSION` | Locally verifiable package/protocol/schema line |

The JSON status response is sent as one JSON document followed by newline and then EOF.
JSON object key order is not contractual. The response contains `protocol_version` but
does **not** contain `schema_version` or package version; those two values are available
only through the local executable's `--version` output at this revision.
