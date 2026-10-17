# PolyWorks API integration

Status: implemented on branch `claude/polyworks-api-integration-l9axft` against
the frozen add-on contract v1 (copy: `docs/polyworks/BLENDER_ADDON_API_CONTRACT.md`).
Not released. No production system was touched. `api.cloth-next.de` is still
closed at the edge, so nothing here has talked to production.

Product rule: **the Companion is the control centre; Blender is a read-only
status display.** Every interactive account, authentication, licence and device
action happens in the existing Cloth NeXt Companion executable.

## Implemented functionality

| Area | Where | Notes |
|---|---|---|
| Contract HTTP client | `cloth_next/polyworks/api.py` | Every v1 endpoint used by the add-on; strict request bodies; unknown response fields ignored; branches on `error.code` only; connect 10 s / read 20 s; no redirects; 1 MiB response cap; HTTPS required except loopback test backends |
| Account workflows | `cloth_next/polyworks/service.py` | Device link (pending / slow_down +5 s / denied / expired / single use), password + MFA, `/me`, licences, redeem, activate (idempotent), devices, deactivate, check-in with renewal and revocation, sign-out (optionally releasing the device), backoff 1/5/15 min then hourly, `Retry-After` |
| Offline verification | `cloth_next/polyworks/entitlements.py`, `ed25519.py`, `trust.py` | Contract 4.3 exactly: split on first `.`, pinned key by `kid`, `typ`, `v == 1`, `inst`, `iat <= now+300`, `now-300 <= exp`; also `prd == "cloth-next"` |
| Shared status machine | `cloth_next/polyworks/status.py` | One pure function used by Blender and the Companion |
| Local storage | `cloth_next/polyworks/storage.py`, `vault.py` | Per-OS-user root, atomic writes, ownership/permission checks, private session vault |
| Companion account window | `companion/account_window.py`, `companion/app.py` (`--mode account`) | Ported from the GAIA owner-branch draft, rebuilt on the frozen contract |
| Headless renewal | `companion/app.py` (`--mode account-sync`) | Check-in when due; launched by Blender, no window |
| Blender dashboard | `cloth_next/blender/account_status.py`, `preferences.py` | Account / License / Connection boxes, one "Open Cloth NeXt Companion" launcher |

### Companion account window

Sections: **Account** (sign in with browser, sign in with email + 2FA, sign out,
refresh, open PolyWorks account), **License** (licence list, editable device
label, activate this computer), **Devices** (list own activations of the seat,
deactivate any of them), **Activation code** (`POST /v1/licenses/redeem`),
**Superhive purchase** (opens the portal: contract 3.2 says order-number claims
are made in the portal; no Superhive call exists in the client). Footer shows
service reachability and last sync. Visual language reuses the Welcome / What's
New buttons, cards, fonts and colours.

### Blender Preferences

Three informational boxes drawn from a cached `StatusView`:

* **PolyWorks Account**: signed-in state, email, session state.
* **License**: status label (Active, Not signed in, No license, Activation
  required, Device deactivated, Offline – license verified, License suspended,
  License revoked, License expired, License could not be verified, Session
  expired, Status unknown), type, seats, devices in use, whether this computer
  is activated, offline licence expiry, the relevant message and warnings.
* **Connection**: whether the Companion is open, service online/offline, last
  licence sync, offline authorization, temporary failures.

The only control is **Open Cloth NeXt Companion** (launches `--mode account`).
It performs no account or licence action. There are no account properties on
`AddonPreferences` (enforced by tests).

## Architecture

```text
                       ┌───────────────────────────── PolyWorks backend (contract v1)
                       │  HTTPS, bearer token        api.cloth-next.de / local test backend
                       │
┌──────────────────────┴───────────────┐
│ Cloth NeXt Companion EXE             │   --mode account       interactive window
│  polyworks.api      HTTP client      │   --mode account-sync  headless renewal
│  polyworks.service  workflows        │
│  polyworks.vault    private session  │──► DPAPI / Keychain / Secret Service / 0600 file
└──────────────┬───────────────────────┘
               │ atomic writes (user-only)
               ▼
   <account root>/installation-id   public, contract 3.3
                  /entitlement       signed token (Ed25519)
                  /state.json        display state (allowlisted, no secrets)
                  /companion.json    presence heartbeat while the window is open
               ▲
               │ stat every 2 s on the timer, read + verify on a worker thread
┌──────────────┴───────────────────────┐
│ Blender add-on                       │
│  blender.account_status  timer, view │   no network, no vault, no mutations
│  polyworks.status/entitlements       │   verifies the entitlement itself
│  Preferences: read-only dashboard    │
└──────────────────────────────────────┘
```

Account root (one per OS user, shared by all Blender versions and add-on builds):
Windows `%LOCALAPPDATA%\PolyWorks\ClothNeXt`, macOS
`~/Library/Application Support/PolyWorks/ClothNeXt`, Linux
`$XDG_CONFIG_HOME/polyworks/cloth-next`. `CLOTH_NEXT_ACCOUNT_ROOT` overrides it
for tests.

## Companion ↔ API

`PolyWorksApi` maps each contract call to a typed result and raises `ApiError`
with the contract code (or a client code: `network_unavailable`,
`service_unavailable`, `server_error`, `invalid_response`). The service treats
`network_unavailable`, `service_unavailable` (any 503, including the edge),
`server_error` (5xx), `feature_disabled` and `internal_error` as **offline**:
the entitlement is kept and the next check is scheduled with backoff. `401` on
any authenticated call discards the token and marks the session expired while
keeping the offline entitlement (contract 2.3, 4.5). `429` honours
`Retry-After`. Server `message` texts are never displayed; every code has
wording in `polyworks/messages.py`.

Configuration: `CLOTH_NEXT_POLYWORKS_API_URL` and
`CLOTH_NEXT_POLYWORKS_PORTAL_URL` (Companion environment) select a local or
staging backend; defaults are the production origins. Plain HTTP is accepted
only for loopback hosts. Browser URLs returned by the server are accepted only
on the API or portal origin.

## Companion ↔ Blender

The existing Bake transport (`LocalSocketServer`) is Blender-owned, single
Companion, per Bake. Account state needs the opposite lifecycle (Companion may
start before or after Blender, several Blenders may run, Blender may start with
no Companion), so the existing file-handoff pattern of the onboarding screens is
reused instead: atomic, user-only files in the per-user account root.

Why this is safe:

* No listening socket; nothing is reachable over the network or loopback.
* POSIX: the folder is 0700, files 0600; Blender ignores files that are not
  regular files owned by the current user or that are group/world-writable.
  Windows relies on the per-user ACL of `%LOCALAPPDATA%`.
* **Authorization never comes from IPC data.** Blender decides "licensed" only
  from an entitlement whose Ed25519 signature it verifies itself against pinned
  keys, for its own installation id and time window. `state.json` can describe
  or downgrade (revoked, session expired, offline), never grant.
* No credential is ever written outside the vault; `state.sanitize` is an
  allowlist used for both writing and reading.

Lifecycle: `account_status.register()` only registers a persistent timer (no
file IO, no process). The timer stats four files (microseconds) and starts one
short-lived worker thread when they change or every 30 s. Draw callbacks only
read the cached view. `unregister()` removes the timer, discards in-flight
results, joins the worker (2 s bound) and terminates Companion processes it
started. Reload is idempotent (stale timer callbacks are removed). Companion
restarts and multiple Blender instances need no coordination: everyone reads the
same files; Companion writers serialise through `account.lock`.

Renewal: when signed in, no Companion window is open, no Bake is active,
Blender is not in background mode and `next_check_at` has passed, Blender
starts `--mode account-sync` (at most every 10 minutes, 120 s timeout). The
Companion checks in under the cross-process lock and exits. No check runs
during a Bake (contract 4.3a).

## Authentication flow

1. Companion: `POST /v1/auth/device/start` (`client_name` like
   `Cloth NeXt 2.9.25 / Blender 5.1.2 (Companion)`).
2. Shows `user_code`, opens `verification_uri_complete` in the browser.
3. Polls `POST /v1/auth/device/token` every `interval` (+5 s on `slow_down`;
   network blips keep polling until the link expires; `access_denied`,
   `expired_token`, `invalid_device_code` end it).
4. Token → vault; then `GET /v1/auth/me`, `GET /v1/licenses`, state published.
5. Blender picks the change up within about 2 s.

Password sign-in is offered as a secondary option for accounts with a password
(contract 2.2). The password is passed straight to the request and cleared from
the UI; it is never stored. `mfa_required` switches to a code field (TOTP or
recovery code).

## Licence verification flow

Activation: `POST /v1/licenses/{id}/activations` → the returned entitlement is
verified locally **before** it is stored; an unverifiable token is never saved
(`untrusted_entitlement`). Check-in: `valid` → verified, then atomically
replaces the stored token; `revoked` / `invalid` → token deleted, reason shown;
an unknown `status` or `reason` is treated as revoked (contract 8).

## Offline behaviour

| Situation | Blender shows | Authorized |
|---|---|---|
| Valid entitlement, last check-in valid and online | Active | yes |
| Valid entitlement, service unreachable / not checked | Offline – license verified | yes |
| Within 5 days of `exp` | warning "Renewal due" | yes |
| `now - 300 > exp` | License expired | no |
| `iat > now + 300` (clock set back) | License could not be verified | no |
| Bad signature, unknown `kid`, other installation, unknown `v` | License could not be verified | no |
| Revoked by check-in | reason-specific status | no |
| No entitlement | Not signed in / No license / Activation required | no |
| Companion not running | Connection: "Not running (not required)" | unchanged |

No offline duration is invented: validity is exactly the signed `exp` plus the
contract's 300 s skew. The 5-day warning is the add-on UI decision the contract
explicitly allows.

Limitations that cannot be solved offline:

* A revocation, refund or deactivation is only seen at the next check-in; until
  then a previously issued entitlement stays valid up to its `exp` (≤ 30 days).
* Clock rollback is only detected down to the stored entitlement's `iat`; a
  clock moved back but still after `iat` extends nothing beyond `exp` but cannot
  be distinguished from real time.
* Copying the account folder to another machine copies the installation id and
  entitlement; the server-side device model is the control for that.

## Secure storage

| Data | Storage |
|---|---|
| Bearer session token | Windows: DPAPI user scope (`session.dpapi`). macOS/Linux: Keychain/Secret Service via `keyring` when installed with a supported backend, else `session.json` 0600 in a 0700 folder (contract 2 fallback) |
| Password, MFA code, device code, MFA token | memory only |
| installation id | `installation-id`, `inst_` + 32 URL-safe random chars, created once with `O_EXCL`, never regenerated when damaged |
| Entitlement | `entitlement`, public signed data |
| Public verification keys | `cloth_next/polyworks/trust.py` (`PRODUCTION_KEYS`), separate from all private data |
| Display state | `state.json`, allowlisted fields only |

No private signing key, no Superhive credential and no custom encryption exists
in the client. Tokens never appear in logs, exceptions, `repr`s, Blender
preferences, `.blend` files or the bake/solver path (tests check public files
and logs).

## Migration behaviour

* `main` had no licence storage, tokens or activation state, so there is nothing
  to migrate. The legacy production stub API is incompatible with v1 and is not
  targeted.
* The unreleased GAIA owner-branch draft (`codex/gaia-owner-2.9.26`) stored a
  UUID installation id and a refresh-token session under Blender's CONFIG
  folder. That format was never issued by a server; this branch does not read
  it. Owner test installs simply sign in again (no device slot was used).
* Stable, Beta and Dev builds share one account root per OS user, so switching
  builds or Blender versions reuses the same installation id and device slot.
* First run after upgrade: Blender shows "Not signed in"; the user signs in in
  the Companion. No existing licence is invalidated because none existed.

## Remaining limitations and open items

1. **Production public key missing.** `PRODUCTION_KEYS` is empty, so no
   production entitlement can be verified yet (fails closed). Needs `key_id` and
   PEM from `GET /v1/entitlements/public-key` of the production backend.
2. OpenAPI snapshot (`addon_v1_openapi.json`) not available to the client repo;
   schemas were implemented from the contract text.
3. No real backend run: E2E ran against `tests/polyworks_backend.py`, an
   isolated contract double. `tools/polyworks_e2e.py` is not provided; run the
   tests with `CLOTH_NEXT_POLYWORKS_API_URL` against the real isolated stack
   when it is reachable.
4. Real Blender was not available in this environment; the CI `blender-smoke`
   job runs `tools/blender_polyworks_status_smoke.py`.
5. Windows DPAPI and the Windows EXE were not executed here (Linux container);
   the DPAPI code is the reviewed owner-branch implementation.
6. Manifest `[permissions] network` text still only mentions solver downloads;
   the Companion launched by the add-on now also contacts PolyWorks. Update
   before release (Blender extension policy wording).
7. No Cloth NeXt feature is gated. `account_status.authorized()` exists for
   future PolyWorks services; deciding what it gates is a product decision.
8. The Superhive preference box still says "Purchase validated" for a local
   library hint; consider "Purchase detected" to avoid implying verification.
9. Session-related security events (passkey removal, 2FA off) follow the server
   (open question 5.3 of the readiness report); the client reacts to 401 only.

## Test results (this environment: Linux, Python 3.12, cryptography 46.0.3)

| Command | Result |
|---|---|
| `pytest -q` (before changes) | 2271 passed, 18 skipped, 1 failed (`test_solver_session.py::test_cancel_recovers_durable_state_when_status_port_closes`, pre-existing on `main`) |
| `xvfb-run -a pytest -q` (after) | see `docs/CODEX_API_INTEGRATION_HANDOFF.md` |
| `pytest tests/test_polyworks_*.py` | 140 passed |
| `ruff check --isolated --select E4,E7,E9,F` on new files | clean (repository has no ruff/black/mypy configuration; existing files are not ruff-clean) |
| `python tools/validate_extension.py cloth_next --phase source` | passed |
| `python companion/build_companion.py` (Linux, PyInstaller 6.21.0) | built |
| `xvfb-run -a python tools/verify_companion.py` | Bake, information and account modes passed |
| Packaged EXE `--mode account-sync` against the contract double | exit 0, entitlement renewed and verified |

Skips: integration tests needing `CLOTH_NEXT_PPF_EXECUTABLE`, Windows-only tests,
migration-artifact tests, as on `main`. Tk window tests skip without a display
(CI `tests` job); they ran here under Xvfb.

## Known issues

* The pre-existing `test_solver_session` failure above is unrelated (it is
  modified on the GAIA owner branch).
* `companion/account_window.py` also exists, differently, on
  `codex/gaia-owner-2.9.26`; merging both branches needs a manual resolution
  (keep this branch's version).
