# Native engine downloads (AETHER, TETHYS): server requirements

> **As built (live 2026-11-07), and what the add-on now uses.** The server implemented a
> different shape than requested below; the client follows the live API:
> - Anonymous: `GET /v1/engines/aether/releases?platform=…` lists only **public** AETHER builds;
>   each artifact carries a direct, token-free `url` on `downloads.cloth-next.de`.
>   GAIA/TETHYS are always empty there.
> - Signed in: `GET /v1/solver/manifest?engine=…&platform=…&backend=…` (`current` + `history`,
>   404 when nothing is published). Dev builds only for dev testers; TETHYS needs GAIA access.
> - Download with a session: `POST /v1/solver/builds/{build_id}/download-session` (Bearer, all engines).
> - `addon_version` and `channel` are not evaluated by the server; the add-on checks the range.
> - Upload: `POST /v1/release/solver/builds` accepts `engine`; build id
>   `<engine>-<version>+<backend>.<platform>.<rev12>`.
> - First uploads 2026-11-07 (audience dev, min add-on 2.9.67): `aether-0.4.6-dev+cuda.windows-x64.04b7db6c9d2d`,
>   `tethys-1.3.0-dev+cpu.windows-x64.1a96f14b650d`, `tethys-1.3.0-dev+cpu.linux-x64.1a96f14b650d`.
>
> The original request follows for reference.

Status: requested 2026-11-07, not implemented on the server. Client side is ready
(Cloth NeXt branch `claude/project-thread-siop6g`). Reference behaviour: the
contract double in `tests/polyworks_backend.py` (`_engine_route`,
`_engine_download`, `publish_engine_build`) and `tests/test_native_engines.py`.

## Why

The AETHER Engine and the TETHYS Engine are no longer shipped inside the
Cloth NeXt add-on ZIP. The add-on downloads, verifies, installs and updates them
from the PolyWorks server, the same way it already does for GAIA.

- **AETHER** is free: no account, no session, no entitlement.
- **TETHYS** needs GAIA access (same decision as GAIA downloads today).

## What already exists (checked 2026-11-07 against production)

`GET /v1/account/downloads` already returns builds with an `engine` field
(`"gaia"`). Only one GAIA build is listed; no AETHER or TETHYS build exists.
The release API (`PUT /v1/release/solver/uploads`, `POST /v1/release/solver/builds`,
header `X-Release-Token`) is used by `tools/release_gaia.py`.

## 1. Release API: accept `engine`

`POST /v1/release/solver/builds` gets one new optional body field:

| field | values | default |
|---|---|---|
| `engine` | `gaia`, `aether`, `tethys` | `gaia` (existing callers unchanged) |

Everything else stays as it is (`upload_id`, `version`, `platform`, `backend`,
`min_addon_version`, `max_addon_version`, `source_revision`, `release_notes`,
`audience`). Values the add-on uses:

| engine | platform | backend |
|---|---|---|
| aether | `windows-x64` | `cuda` |
| tethys | `windows-x64`, `linux-x64` | `cpu` |

- `source_revision` is the **engine** repository commit (AETHER uses 12 hex
  characters, TETHYS 40). Accept 7–40 lowercase hex.
- Uniqueness: engine + version + platform + backend + source_revision; a repeat
  is `409 build_exists` (as today).
- Uploaded files are ZIPs of about 0.3–3 MB. No content inspection is needed;
  the add-on verifies the package itself.
- `build_id` format is the server's choice; suggested
  `<engine>-<version>+<backend>.<platform>.<revision[:12]>`.
- The release token can still neither delete builds nor change audiences.
- Administration: list/delete/dev↔public for engine builds like GAIA builds,
  filterable by engine.

## 2. Client API

### 2.1 List builds

`GET /v1/engines/{engine}/builds?platform=<p>&addon_version=<v>&channel=<dev|public>`

- `engine` ∈ `aether`, `tethys`; anything else `404 not_found`.
- **aether:** no `Authorization` header is sent. Must work anonymously.
  `channel=public` → only public builds. `channel=dev` → dev and public builds
  (the dev add-on is itself publicly downloadable, so there is nothing to protect).
- **tethys:** Bearer session required (`401 not_authenticated` without one).
  No GAIA access → `403 gaia_access_required`. Visibility of dev builds follows
  the GAIA rule (dev release list); the `channel` parameter is ignored for tethys.
- Filtering (important, differs from today's GAIA list): return, **per
  platform+backend, the newest build whose add-on range contains
  `addon_version`** (`min_addon_version <= addon_version <= max_addon_version`,
  numeric comparison of the `X.Y.Z` part). A newer build that needs a newer
  add-on must not hide the build an older add-on can still use.
- Unknown/missing `platform` → empty list (not an error).
- Response `200`:

```json
{"builds": [{
  "engine": "aether", "build_id": "aether-0.4.6-dev+cuda.windows-x64.04b7db6c9d2d",
  "version": "0.4.6-dev", "channel": "dev", "platform": "windows-x64", "backend": "cuda",
  "sha256": "<64 hex of the uploaded ZIP>", "size_bytes": 3018818,
  "min_addon_version": "2.9.67", "max_addon_version": null,
  "release_notes": "…", "published_at": "2026-11-07T09:00:00Z"}]}
```

Required per build: `build_id`, `version`, `sha256`, `size_bytes`; the rest as
for GAIA builds. Unknown fields are ignored by the client.

- Rate limit for anonymous calls: suggested 60/hour per IP; answer `429
  rate_limited` with `Retry-After`. The add-on asks once per Blender start.

### 2.2 Download session

`POST /v1/engines/{engine}/builds/{build_id}/download-session` (empty body)

- Same authorisation as 2.1 (aether anonymous; tethys session + GAIA access,
  re-checked now).
- Unknown build, wrong engine or withdrawn → `404 not_found`.
- Response `200` exactly like the GAIA session:
  `{"url": "https://downloads.cloth-next.de/…?t=…", "sha256": "…", "size_bytes": 3018818, "expires_at": "…"}`
- The URL **must** be HTTPS on `downloads.cloth-next.de` (the client refuses
  any other host). `Range` requests should work (resume); without them the client
  restarts from zero.
- Edge errors as for GAIA: `403 forbidden`, `403 download_expired` (client asks
  for a fresh session and resumes), `403 gaia_access_required` (tethys, access
  revoked; client stops and deletes the partial file), `403 build_unavailable`.

### 2.3 `/v1/account/downloads`

May keep returning only GAIA builds. If it also returns tethys builds, that is
fine: the add-on ignores every build whose `engine` is not `gaia` there.

## 3. Package format (for information; the server does not inspect it)

ZIP with exactly two files, written by `tools/release_engine.py`:

- `engine.json`: `kind` `ClothNeXt.NativeEngine.v1`, `schema_version` 1,
  `engine`, `version`, `build_id` (engine's own id), `commit`, `platform`,
  `filename`, `file_size`, `sha256` (of the binary), `protocol` (+ `api` for AETHER).
- `bin/AETHER Engine.exe`, `bin/TETHYS Engine.exe` or `bin/tethys-engine`.

## 4. Acceptance checks

1. Upload with `engine=aether` → anonymous `GET /v1/engines/aether/builds?platform=windows-x64&addon_version=2.9.67&channel=dev` lists it.
2. Same with `channel=public` hides a dev build.
3. `addon_version` below `min_addon_version` → not listed; an older compatible build is listed instead.
4. Anonymous download session for aether works and the file's SHA-256 equals `sha256`.
5. tethys list without session → 401; with session but no GAIA access → 403 `gaia_access_required`; with access → listed.
6. Existing GAIA upload/list/download unchanged (no `engine` in the upload body = gaia).
