# Cloud Bakes API v1 — Phase-0 add-on handoff, 23 October 2026

[OpenAPI](CLOUD_BAKES_OPENAPI_V1.json) and [schema-validated examples](CLOUD_BAKES_EXAMPLES_V1.json)
are checked into this review tree. This revision supersedes the earlier role-keyed file map.
It defines the HTTP mapping for the separate add-on's `CloudBackend`; no add-on code is changed.

**Execution architecture:** the website is exclusively the control plane: account auth,
package storage, upload/download and RunPod orchestration. All future native GPU work
belongs on RunPod. Pods versus Serverless is undecided; the RunPod adapter prepares an
immutable dispatch description but rejects dispatch/status/cancel operations. No RunPod
requests, GPU creation, provider charges or native processes are started by the website.
Submission remains disabled outside explicit synthetic/test-harness execution.

## Hosts and account authentication

| Purpose | Origin |
|---|---|
| API | `https://api.cloth-next.de` |
| Package and result downloads | `https://api.cloth-next.de` |

The existing configured `API_ORIGIN` is authoritative and must be an HTTPS origin without
credentials, path, query or fragment. Capabilities expose `transport.api_origin` and
`transport.download_origin`, both this origin. Generated URLs are absolute and derive
from configuration, never the request's Host header or a client URL. There are no foreign
redirects or RunPod/provider URLs in client download responses. The add-on must allowlist
these origins, require HTTPS and reject redirects. These are the declared contract hosts;
production Cloud routing is not enabled by this task.

Use the existing PolyWorks bearer session. Portal cookies keep the existing Origin/CSRF
rules. Every operation checks authenticated account ownership. Download URLs additionally
carry a short-lived session token; a URL/token alone is insufficient. Downloads keep
`Content-Encoding: identity`, SHA-256 ETag, `Cache-Control: no-store, no-transform` and HTTP
Range 206/416. Offset/length maps to `Range: bytes=offset-(offset+length-1)`.

## Endpoints

All paths are relative to `/v1/cloud`. OpenAPI defines the complete required/optional
fields, bounds, response types and the existing error envelope.

| Method | Path | Request / response |
|---|---|---|
| GET | `/capabilities` | `CapabilitiesOut`, explicit disabled RunPod state, desktop/server builds, transport, limits |
| GET | `/account` | `AccountOut`, entitlement and submission eligibility; no invented credits |
| POST | `/quotes` | `Create` → 201 `QuoteOut`, test-only immutable maximum |
| POST | `/jobs` | `Create` + `Idempotency-Key` → 201 `JobOut` |
| GET | `/jobs?after=UUID&limit=50` | `ListOut`, own jobs, max 100, UUID cursor |
| GET | `/jobs/by-client-id/{client_job_id}` | `JobOut`, including deletion tombstones |
| GET | `/jobs/{job_id}` | `JobOut` |
| POST | `/jobs/{job_id}/uploads` | `UploadIn {role,name}` → 201 `UploadOut` |
| GET | `/uploads/{upload_id}` | `UploadOut`, durable raw-byte offset/status |
| PATCH | `/uploads/{upload_id}` | binary chunk, `Upload-Offset`, `Chunk-SHA256`, optional `Content-Encoding: zstd` |
| POST | `/uploads/{upload_id}/complete` | full decoded SHA/size verification → `UploadOut` |
| POST | `/jobs/{job_id}/submit` | `Submit {quote_id,accepted_maximum}` + `Idempotency-Key` → `JobOut` |
| POST | `/jobs/{job_id}/cancel` | idempotent cancellation → `JobOut` |
| DELETE | `/jobs/{job_id}` | idempotent 204, terminal/unsubmitted jobs only |
| POST | `/jobs/{job_id}/result-session` | `ResultOut`, raw result manifest, file URLs and snapshot convenience downloads |
| GET | `/jobs/{job_id}/results/{sha256}?t=...` | binary Range download, result-scoped token + account |
| POST | `/jobs/{job_id}/package-session` | `PackageOut`, original manifest and every retained package file |
| GET | `/jobs/{job_id}/files/{sha256}?t=...` | binary Range download, package-scoped token + account |
| GET | `/jobs/{job_id}/log` | sanitized fixed codes/statistics; no native stdout/stderr |

`package-session` requires a complete retained package. It is not tied to a Blender
session, active execution entitlement, available credits or RunPod availability. All
package files, including `import-snapshot` and `snapshot-arrays`, remain referenced for
at least the result retention period. Package/result session tokens have distinct scopes;
a package token cannot be moved onto a result URL. Deletion revokes access. No `.blend`
is needed for cross-machine snapshot recovery.

## Manifest and files

`Create` contains `manifest` directly; there is no separate manifest upload. The existing
flat v1 fields remain: schema version 1, client job UUID, add-on/Blender versions, engine,
solver version, build identity, exact protocol and payload schema, frame start/end/FPS,
quality/determinism, water mode, objects, fingerprints, snapshot digest and files.
The server hashes canonical sorted-key compact JSON as `manifest_sha256` for binding
quotes and immutable job identity. This is the HTTP manifest schema; a future native
package-envelope alias is not silently accepted. A real exported package fixture is
still needed to prove the add-on-to-HTTP translation and canonical byte parity.

`files` is now a **list**, each entry:

```json
{"role":"hydro-volume","name":"hydro/000001/volume.bin","size_bytes":12582912,"sha256":"<64 lowercase hex characters>"}
```

SHA-256 and `size_bytes` always describe **uncompressed raw bytes**. Identity is
`(role,name)`, not role or content hash alone. Names use only `[A-Za-z0-9._/-]`, are
relative, have no empty, `.` or `..` components, no backslashes/drive letters/absolute
paths, and no trailing slash. `manifest.json` is reserved for the create-supplied
manifest. Duplicate identities and file/directory collisions within a role are rejected.
Names in different roles may be the same. CAS uses owner UUID and content hash, never
these client names; references use a fixed-length digest of `(role,name)`.

| Role | Cardinality | Native use |
|---|---|---|
| `data` | exactly 1 | unchanged scene payload |
| `param` | exactly 1 | unchanged parameters, including native pre-encoded fluid sequence |
| `official-bindings` | exactly 1 for protocol `0.26`, both GAIA and AETHER | apply after BUILD |
| `hydro-model` | exactly 1 in hydrodynamic mode | sanitized JSON model |
| `hydro-volume` | 1 or more in hydrodynamic mode | supplied relative volume structure |
| `import-snapshot` | exactly 1 | opaque retained import metadata, never native execution input |
| `snapshot-arrays` | 0 or more | opaque retained binary arrays, never execution input |
| `fluid` | optional legacy sidecar | never required merely because `water_flow=true` |

`water_flow=true` with `water_mode=native-fluid` needs no `fluid` file: its sequence is
already in `param`. For compatibility `water_flow=true` with the default `water_mode=none`
also accepts native param flow, never requiring a sidecar. New clients should send the
explicit mode. `water_mode=hydrodynamic` requires GAIA, `water_flow=true`, a model and
volumes. Hydro files are rejected in other modes. The old `0.23` held-stream water
protocol is rejected with `water_mode_unsupported`; it requires a local live helper.
The FLIP cache is never an allowed package role and must never be uploaded. The add-on
must remove its local FLIP path from hydrodynamic provenance before hashing/packaging.

`import_snapshot_digest` must match the declared `import-snapshot` file SHA-256; it does
not hash native solver binaries or implicitly concatenate snapshot arrays. Arrays carry
their own declared hashes. All declared files must be complete before submit.

Cloud manifests/quotes have a separately bounded body limit (default 2 MiB), needed for
hundreds of volume entries. Existing account endpoints retain their original body limit.
Capabilities advertise raw chunk, wire chunk, manifest, package/file and account limits.
Technical size limits are operator-configurable, not commercial storage offerings.

## Exact build identities

The add-on sends the following **three required fields**, plus engine/version/protocol/schema:

| Field | GAIA | AETHER |
|---|---|---|
| `build_id` | exact `build_id` from `gaia-build.json` | official upstream release tag |
| `solver_sha256` | SHA-256 of the selected `ppf-cts-server.exe` / Linux `ppf-cts-server` | same executable definition |
| `worker_sha256` | SHA-256 of selected `ppf-contact-solver.exe` / Linux `ppf-contact-solver` | same native worker definition |

Normally both native binaries are in `target/<backend>/release/`. Worker resolution
follows the add-on: beside the server first, then `target/release/`, using the platform's
worker filename. Hash the actual selected raw files, not their ZIPs, paths or filenames.
GAIA's `SHA256SUMS.txt`/worker entry in `gaia-build.json` can be checked, but a descriptor
string alone does not replace content verification. Abbreviated example hashes are not
accepted as real identities.

`ppf/official_scene_worker.py` belongs to the add-on. It is **not** the native worker
binary and never supplies `worker_sha256`. A reviewed copy belongs in the pinned RunPod
adapter image, identified independently by `bindings_bridge_sha256` in the operator
runtime registry. Neither that script's contents nor arbitrary executable paths are
accepted from a client manifest.

Capabilities list `engines[].desktop_builds[]` with all three desktop identity fields.
Each offered entry maps explicitly to `server_build`, `server_solver_sha256`,
`server_worker_sha256`, server artifact hash and an internally pinned OCI image digest.
Engine, version, both native desktop hashes, build ID, protocol/schema and water support
must all match a configured mapping. No cross-platform equality or fallback is assumed.
No verified Linux mappings are shipped. The default offered build list is empty.

Results report `build_id` of the **executed server build**, actual server
`solver_sha256`/`worker_sha256`, server artifact hash and protocol. They must match the
pinned server mapping; the desktop hashes are not reused as if Linux binaries matched.
`server_build` remains an explicit equal-valued legacy metadata field.

## Upload, compression and recovery

Open one session per `(role,name)`. Reopening the same file returns the existing open
session; verified account-owned content is attached with `reused:true`, no upload ID,
`received=size_bytes`, `confirmed:true`. Other accounts cannot probe/reuse that storage.
Status contains role/name, session ID, `offset` and equivalent `received` raw-byte count,
`completed`, `confirmed`, size and expiry. `confirmed` means final whole-file verification,
not merely a chunk ACK. Confirmed offset advances only after chunk persistence/DB commit.

Send `Content-Type: application/octet-stream`. Identity encoding is the default; zstd
level 1 is supported/recommended. **Every PATCH is one independent zstd frame**; do not
continue a compressor stream across requests. `Upload-Offset` and `Chunk-SHA256` refer
to decoded bytes. Final hash/size verification is also over decoded raw bytes. Identical
confirmed offset/length/hash replays are ignored even after a lost response; conflicting
bytes or out-of-order writes return `offset_mismatch`. GET status and resume at its offset.

Both compressed wire bytes and decoded bytes/window memory are bounded. The advertised
wire budget includes overhead for incompressible chunks. Truncation, malformed streams,
trailing/concatenated frames, oversized decoded headers, oversized windows and unsupported
encodings are rejected without advancing the offset. Compression level cannot reliably
be recovered from the encoded bitstream; the server accepts valid bounded zstd and the
client chooses level 1. Raw result frames are not compressed by this transport.

Use stable keys: create may use `client_job_id`, submit `<client_job_id>:submit`. Keys
are account-global, persist without time expiry and cannot be reused for another body
or operation. Lookup by client ID recovers a lost create/submit response. Accepted retries
return the original job's current state, never another job/accounting record. Different
content returns `idempotency_conflict`. Cancel is idempotent. Deletion retains permanent
client-ID/hash/receipt tombstones and cannot resurrect an execution.

## Native execution and raw results

RunPod topology is not selected, so these are adapter requirements plus tested helper
behavior, **not a claim of real backend GPU execution**:

1. Materialize execution files as `/input/<role>/<name>`. Keep every supplied hydro
   relative name unchanged below `/input/hydro-volume/`. Snapshot roles are withheld.
2. BUILD the native project using the real solver installation.
3. Invoke that installation's Python with the reviewed add-on bridge:
   `ppf/official_scene_worker.py --project-name <server-owned name> --project-root <job root> --bindings <materialized official-bindings file>`.
4. Run with `PPF_EXPERIMENTAL_HYDRO_TEST_JSON` pointing to `/input/hydro-model/<name>`
   and `PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR=/input/hydro-volume` for hydrodynamic mode.
5. Collect unchanged raw output. No Blender import, PC2, coordinate conversion or FLIP
   preparation happens on the website or in this API layer.

`native.execute_phases` tests BUILD/bindings/solve ordering and separate native-binary/
bridge identities. It requires an explicit RunPod test context. The real SolverSession,
packaged bridge and Linux installation remain to be integrated in an approved RunPod
image; `tools/cloud_phase0.py` and native sources were not available here.

`NativeResult` contains Vertex-Map, Surface-Map **only for SOLID**, execution metadata and
contiguous raw frame blocks. Each file has SHA-256 and raw size; each frame block has
inclusive `first_frame`/`last_frame`. The packing helper concatenates validated
`vert_<N>.bin` files unchanged in groups of 25, named `frames-NNNNNN-MMMMMM.bin` (last
block may be shorter). The native adapter must prove each frame complete before packing.
No extra headers, compression or PC2 bytes are inserted.

The result also includes `build_id`, native server hashes, `import_snapshot_digest`,
`completed_frame`, `completed_frame_count`, and `partial:false`. The server validates
identity, hashes, sizes, required maps and continuous coverage from requested start
through end. Unknown live frame/ETA/queue progress remains null. Partial results are
currently rejected and not advertised until native validation is proven.

States persist: `uploading → queued → starting → running → succeeded/failed`, cancellation
through `cancelling → cancelled`, `stopped_at_limit` for the isolated test ceiling and
`expired` for retention. Unsubmitted/queued jobs may cancel immediately. Native process
termination must be confirmed before terminal state/input release; uncertain worker leases
are fenced and never automatically rerun. These paths are currently test-harness behavior.

## Costs and errors

Commercial quoting/billing is disabled. Isolated test quotes bind manifest/runtime,
expiration and accepted maximum in test seconds. Measured elapsed time and unique
accounting remain persistent; they are not an enforceable RunPod/provider spending cap.
Production submit returns 503 while RunPod deployment is undecided; no charge/accounting
acceptance occurs. No quota/credit balance, currency/rates or commercial retention is chosen.

Existing error envelope: `{error:{code,message,request_id?,details?}}`. The client should
map codes and show its own localized messages/request ID, never server messages. Unknown
codes may fall back to CNX-E200. Network errors/invalid HTTP responses are client transport
conditions, not fabricated server-side errors.

| Status | Codes |
|---|---|
| 400 | `bad_request`, `integrity_mismatch`, `invalid_chunk`, `invalid_file_role`, `invalid_storage_path`, `idempotency_key_required`, `water_mode_unsupported` |
| 401/403 | `not_authenticated`, `forbidden`, `insufficient_scope`, `session_expired` |
| 404 | `not_found` (including another account's resource) |
| 409 | `idempotency_conflict`, `engine_build_unavailable`, `invalid_state`, `invalid_quote`, `upload_incomplete`, `offset_mismatch`, `content_corrupt`, `conflict` |
| 410 | `upload_expired`, `result_unavailable` |
| 413/415 | `payload_too_large`, `package_too_large`, `invalid_chunk` / `invalid_content_type` |
| 416 | binary unsatisfiable Range response |
| 422 | `validation_error`, sanitized details without echoed input |
| 429/503 | `rate_limited`, `account_limit` / `feature_disabled` |

Neither persistent logs nor diagnostics contain credentials, native stdout, query `t`
or download-session bodies. Existing B14/E-code equivalence still needs comparison with
the authoritative concept list, which is not available in this workspace.