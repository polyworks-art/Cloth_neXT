# Verified PPF protocol

Status: observed at official commit `7193f158`, protocol `0.11`. This is an
implementation contract to pin and test, not a promise of upstream stability.

## Server start and readiness

The native executable is `ppf-cts-server.exe` on Windows. Its verified arguments are:

```text
ppf-cts-server.exe --host 127.0.0.1 --port 9090 \
  --progress-file <absolute-path> [--debug]
```

Defaults are host `127.0.0.1`, port `9090`, and `progress.log` in the working
directory. Startup truncates the progress file, appends `SERVER_STARTING`, binds the
TCP listener, then appends `SERVER_READY`. Readiness is not complete until both marker
and a successful protocol query agree. `--version` includes package, protocol and
schema versions. The manager must pass an argument list, reserve/check the port, own
the process handle, capture stdout/stderr, and terminate/reap it during shutdown.

The official Windows development path builds with Cargo and expects
`target/release/ppf-cts-server.exe`; the documentation also describes `start.bat` for
a prepared checkout. Cloth NeXt must discover a packaged executable explicitly and
must not assume a source checkout.

## Transport framing

The server is a TCP service. Protocol compatibility is exact: client and server must
both report `0.11`.

- `TCMD`: four ASCII bytes, then a 4-byte unsigned big-endian payload length, then a
  UTF-8 command argument string. Response is newline-terminated JSON.
- `JSON`: four ASCII bytes, then one newline-terminated UTF-8 JSON header. Depending on
  `request`, raw payload bytes follow or the server replies with a JSON metadata line
  followed by exactly `size` raw bytes.
- `BDAT`: a legacy/test stub acknowledged as `BINARY_OK`; not a production path.
- transfer chunks are 32 KiB in the official client.

Phase 3A implements status plus typed `build`, `cancel_build`, `start`,
`terminate`, and `delete` requests. Its payload is `--name <project>` with an
optional `--request <value>`. The client bounds the response to 1 MiB by
default and applies separate connect/read timeouts. Golden bytes and source provenance
are under `tests/fixtures/ppf_0_11/`.

The status response contains `protocol_version` but no schema or package-version field.
Those are verified for an owned local executable through `--version`; they cannot be
proven for an arbitrary external PPF 0.11 server through this query.

Verified JSON request selectors are `upload_atomic`, `upload_notify`, `data_send`,
`data_receive`, `notebook_send`, and `notebook_delete`. Scene transfer uses
`upload_atomic`; co-located official clients can instead write the two payload files
directly and send `upload_notify`. Cloth NeXt should initially use `upload_atomic` so
the server owns atomic publication, unless the direct-disk contract is separately
implemented and tested.

The payloads historically retain filenames `data.pickle` and `param.pickle`, but at
protocol 0.11 their content is a CBOR envelope encoded with `cbor2`, not Python pickle.
The envelope is `{version: 1, kind: "Scene"|"Param", payload: ...}`.
Both data and parameter SHA-256 hashes plus an add-on-minted upload ID participate in
transfer/status tracking. Cloth NeXt must reproduce the schema independently from the
published format definitions; it must not import the official add-on.

## Lifecycle and results

TCMD always includes `--name <project>`. With no `--request` it is a status query.
The exact request values accepted by the audited server are `build`, `cancel_build`,
`start`, `resume`, `terminate`, `save_and_quit`, and `delete`. `resume` optionally
carries `--resume_from <i32>`; `build` optionally carries
`--preserve_output 1`. Because these spellings and response fields are versioned
protocol, the adapter must use typed requests and fixtures captured from upstream
tests rather than concatenate free-form strings throughout the application.

Server wire statuses verified at this revision are:

```text
NO_DATA NO_BUILD BUILDING READY RESUMABLE FAILED BUSY SAVE_AND_QUIT
```

Output is stored per project on the solver host. The client first fetches an output
map (object UUID to vertex ranges), then complete frame files. The official client can
request only frames not already fetched and can request only the latest frame.
Therefore **incremental frame retrieval is supported**. A frame must be treated as
available only after the server reports/publishes it complete.

Fetched vertex arrays are converted into PC2 (`POINTCACHE2`, version 1, little-endian
float32 XYZ, constant vertex count) by the official add-on and played through Blender's
Mesh Cache modifier. PC2 is a client cache format, not the network frame format.

## Release/update observations

Phase 3A fetches `session/map.pickle`, a CBOR `VertexMap` envelope mapping
object UUIDs to global output indices, then
`session/output/vert_<N>.bin`: raw little-endian float32 XYZ triples in solver
world space. It validates exact byte counts, finite coordinates, UUIDs, index
order, and vertex count. The implemented status contract consumes `status`,
`data`, `frame`, `initialized`, `error`, `root`, `upload_id`, `data_hash`,
`param_hash`, and `protocol_version`, plus optional `progress` and `info`.

The official add-on has a stable Blender Extension feed at
`releases/download/addon-latest/index.json`. A mutable `addon-latest` index points to
immutable `addon-YYYY-MM-DD-HHMM` ZIP releases. This describes **add-on** updates.

At the audited revision no documented stable/experimental standalone Windows solver
manifest with version, URL, SHA-256, archive layout and rollback metadata was found.
CI builds solver artifacts and the documented Windows workflow builds from source,
but a CI artifact is not a supported update API. Cloth NeXt must not use the add-on
index as a solver feed or invent URLs. Updater implementation is blocked until an
official solver distribution contract or a Cloth NeXt-owned signed manifest is
defined.

## Primary sources

- [Server CLI/startup](https://github.com/st-tech/ppf-contact-solver/blob/7193f158e3843597070f66cb29af19efd9bdcff7/crates/ppf-cts-server/src/main.rs)
- [Wire constants and framing](https://github.com/st-tech/ppf-contact-solver/blob/7193f158e3843597070f66cb29af19efd9bdcff7/crates/ppf-cts-server/src/protocol.rs)
- [Protocol version contract](https://github.com/st-tech/ppf-contact-solver/blob/7193f158e3843597070f66cb29af19efd9bdcff7/crates/ppf-cts-server/src/lib.rs)
- [Official client protocol](https://github.com/st-tech/ppf-contact-solver/blob/7193f158e3843597070f66cb29af19efd9bdcff7/blender_addon/core/protocol.py)
- [PC2 implementation](https://github.com/st-tech/ppf-contact-solver/blob/7193f158e3843597070f66cb29af19efd9bdcff7/blender_addon/core/pc2.py)
- [Installation/release feed](https://st-tech.github.io/ppf-contact-solver/blender_addon/getting_started/install.html)
