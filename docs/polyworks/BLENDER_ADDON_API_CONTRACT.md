# Blender add-on API contract (v1) — client-side copy

> Copy of the frozen server document `BLENDER_ADDON_API_CONTRACT.md`
> (`/opt/cloth-next/docs/` on the backend host), supplied by the product owner
> on 2026-10-17. The server copy and the OpenAPI snapshot
> `backend/tests/contract/addon_v1_openapi.json` remain authoritative. Do not
> edit this file to change behaviour; changes require coordination with the
> backend and a regenerated snapshot.

Status: FROZEN for the add-on integration (2026-10-17). Implemented and tested on
the new backend (migration 0006); not deployed. Production still runs the legacy
stub API.

Every endpoint below exists in code and is tested in backend/tests/ (281
passing), including an end-to-end HTTP run against an isolated stack. The
OpenAPI definition of each contract endpoint, with all its schemas, is frozen in
backend/tests/contract/addon_v1_openapi.json. test_addon_contract_frozen fails on
any difference. Any change, even an additive one, requires coordination with the
add-on team, then a deliberate regeneration of the snapshot
(`python -m tests.contract_snapshot --write`).

## 1. Base URL and versioning

Base URL: `https://api.cloth-next.de` (portal: `https://account.cloth-next.de`).
Make it configurable in the add-on for local and staging tests.

Availability: api.cloth-next.de currently answers 503 service_unavailable at the
edge. Opening it is a separate, not yet approved step after the backend cutover.
Until then, test against a local backend (for example http://127.0.0.1:8000).
Treat 503 from the edge like any other network failure (section 7).

All paths are under /v1. Breaking changes get a new prefix (/v2); /v1 stays
available during a transition period.

Requests and responses are JSON (Content-Type: application/json). Timestamps are
ISO 8601 UTC. IDs are UUIDs.

Request bodies are strict: unknown fields are rejected with 422
validation_error. Do not send extra fields.

While the contract is frozen, responses do not change without coordination.
After the freeze, /v1 may gain optional response fields, so the add-on must
ignore unknown response fields from the start.

## 2. Authentication

The add-on always uses bearer tokens (`Authorization: Bearer <token>`), never
cookies. A bearer session is of kind addon: it lasts 90 days at most and expires
after 30 days without use (both configurable on the server). Store the token in
the OS keyring if possible, otherwise in a user-only file. Never log it.

### 2.1 Recommended: device link (works for passkey-only and 2FA accounts)

`POST /v1/auth/device/start`

```json
{"client_name": "Cloth neXt 3.0 / Blender 5.2"}
```

→ 200

```json
{"device_code": "<secret, keep in memory>", "user_code": "BCDF-GHJK",
 "verification_uri": "https://account.cloth-next.de/link",
 "verification_uri_complete": "https://account.cloth-next.de/link?code=BCDF-GHJK",
 "expires_in": 600, "interval": 5}
```

Show user_code and open verification_uri_complete in the browser. The person
signs in to the portal and approves the request there.

Poll `POST /v1/auth/device/token` with `{"device_code": "..."}` every interval
seconds:

| Response | Meaning | Add-on action |
|---|---|---|
| 200 `{"status":"authenticated","token":"…","expires_at":"…"}` | approved | store the token, stop polling |
| 400 authorization_pending | not decided yet | keep polling |
| 400 slow_down | polled too fast | add 5 s to the interval, keep polling |
| 400 access_denied | person declined | stop, show a message |
| 400 expired_token | 10 minutes passed | stop, offer to start again |
| 400 invalid_device_code | unknown or already collected | stop |

The token is returned once. Device codes are single use.

### 2.2 Password login (accounts with a password)

`POST /v1/auth/login`

```json
{"email": "user@example.com", "password": "…", "token_mode": "bearer"}
```

- 200 `{"status":"authenticated","token":"…","expires_at":"…"}`
- 200 `{"status":"mfa_required","mfa_token":"…"}` → `POST /v1/auth/login/mfa`
  with `{"mfa_token":"…","code":"123456","token_mode":"bearer"}` (TOTP or
  recovery code; 5 attempts per mfa_token).
- 401 invalid_credentials for a wrong email or password; the response does not
  reveal which one was wrong.

### 2.3 Session endpoints

- `GET /v1/auth/me` → the account (id, email, email_verified, …). 401 means the
  token is dead: discard it and sign in again.
- `POST /v1/auth/logout` → 204. It ends this session only.
- `GET /v1/auth/sessions` lists sessions with kind (web or addon). The person can
  end add-on sessions from the portal; the add-on then gets 401.

## 3. Licences, seats and devices

Model: a licence is one purchase. seats is the number of purchased user seats.
Each user with a seat can activate up to device_limit (default 3) devices
(installations). Seats are assigned in the portal; releasing a seat is
admin-only.

### 3.1 Status: `GET /v1/licenses`

Returns the licences on which the caller holds a seat. `[]` means no licence.

```json
[{"id": "7c1e…", "product_id": "cloth-next", "tier": "commercial", "seats": 3,
  "purchase_provider": "superhive", "state": "active", "created_at": "…",
  "purchase_status": "valid", "role": "owner", "seat_id": "…",
  "devices_active": 1, "device_limit": 3, "usable": true}]
```

- state: active | suspended | revoked.
- purchase_status: valid | refunded | disputed.
- usable: the only field the add-on needs for decisions. true means new
  activations and renewals are possible.

### 3.2 Redeeming an activation code

`POST /v1/licenses/redeem {"code": "…"}` → 201 plus the licence. The email
address must be verified, otherwise 403. Superhive order-number claims are made
in the portal, not the add-on.

### 3.3 Device identification

installation_id: generated once per Blender installation and user profile, then
stored locally (for example `inst_` followed by 32 random URL-safe characters).
Allowed characters: `[A-Za-z0-9_-]`, length 16 to 128. Do not derive it from
hardware serials or personal data. Reinstalling with a new id uses a new device
slot.

### 3.4 Activate: `POST /v1/licenses/{id}/activations`

```json
{"installation_id": "inst_…", "label": "Workstation"}
```

→ 200 `{"activation": {"id","installation_id","label","activated_at","last_seen_at"}, "entitlement": "<token>"}`

The call is idempotent per installation: activating again refreshes the
entitlement and does not use a new slot.

Errors:

- 409 device_limit: the seat already has 3 active devices.
- 409 license_inactive: the licence is suspended, revoked or refunded.
- 403: no seat on this licence.
- 404: no such licence.

### 3.5 List and deactivate devices

- `GET /v1/licenses/{id}/activations` lists the caller's own devices on that seat.
- `DELETE /v1/licenses/{id}/activations/{activation_id}` → 204. This frees the
  slot, and check-ins from that device then report revoked.

## 4. Offline entitlement

### 4.1 Format

`<base64url(payload_json)>.<base64url(ed25519_signature)>`, base64url without
padding. The signature covers the exact decoded payload bytes.

```json
{"typ": "polyworks.entitlement", "v": 1, "kid": "<key id>", "lic": "<license uuid>",
 "sub": "<user uuid>", "act": "<activation uuid>", "inst": "<installation_id>",
 "prd": "cloth-next", "tier": "commercial", "seats": 3, "seat": "<seat uuid>",
 "iat": 1792000000, "exp": 1794592000}
```

### 4.2 Public key

`GET /v1/entitlements/public-key` → `{"key_id": "…", "public_key_pem": "-----BEGIN PUBLIC KEY-----…"}`
(Ed25519). If signing is not configured, the endpoint returns 503
feature_disabled.

Ship the public key inside the add-on (pinned by key_id). Do not trust a key
fetched at runtime on its own, because that defeats offline verification. A key
rotation will publish a new kid, so the add-on should accept a small set of
pinned keys.

### 4.3 Client verification (all checks required)

1. Split on the first `.`, decode both parts, and verify the signature with the
   pinned key for kid.
2. `typ == "polyworks.entitlement"` and `v == 1`. Reject unknown v.
3. inst equals the local installation_id.
4. `iat <= now + 300` and `now - 300 <= exp`.

The reference implementation is backend/app/security/signing.py:
verify_entitlement.

### 4.3a Licensing rules for the client (binding, from docs/licensing.md)

- A PolyWorks licence covers PolyWorks services. The add-on code is
  GPL-3.0-or-later, and the engines are Apache-2.0. Describe checks in those
  terms; never present them as a restriction on the GPL software itself.
- Never interrupt a running simulation because of licensing: no checks during a
  bake and no network dependency during a bake. A server outage only delays
  renewal.
- An expired or revoked entitlement must never cause data loss.

### 4.4 Expiry and renewal

- Entitlements are valid for 30 days.
- While online, call check-in (section 4.5) at start-up and then at most every
  few hours. Each valid response returns a fresh 30-day entitlement; replace the
  stored one.
- Offline, the add-on keeps working until exp. After exp it must go online.
- The server does not impose a separate grace period. Any additional UI warning
  (for example from day 25) is an add-on decision.

### 4.5 Check-in: `POST /v1/entitlements/check-in`

```json
{"entitlement": "<stored token>", "installation_id": "inst_…"}
```

Responses (always 200, Cache-Control: no-store):

| status | reason | Add-on action |
|---|---|---|
| valid | null | store the returned entitlement; license carries the section 3.1 status |
| revoked | refunded, license_revoked, license_suspended, seat_released, device_deactivated | delete the stored entitlement; show the reason; offer re-activation where it makes sense (device_deactivated) |
| invalid | null | the token is not ours, tampered with, or belongs to another account or installation: delete it; activate again |

Errors:

- 401: the session is dead (logout, session ended in the portal, password reset,
  account suspended or deleted). Keep the offline entitlement until exp, and ask
  the person to sign in.
- 503 feature_disabled: signing is off. Treat this like a network failure.

### 4.6 Account deletion (portal only)

People delete their account in the portal; the add-on has no delete endpoint (a
bearer token gets 403). Effects the add-on sees:

- every session ends at once, so the next request returns 401;
- the person's seat is released and their activations are revoked, so a check-in
  from a new account reports invalid (different account);
- the licence itself is kept; the buyer recovers it with a new account through a
  reviewed purchase claim in the portal.

## 5. Errors

Every error has this shape:

```json
{"error": {"code": "device_limit", "message": "human readable", "request_id": "…", "details": […]}}
```

Branch on code, never on message.

| HTTP | code | Notes |
|---|---|---|
| 400 | authorization_pending, slow_down, access_denied, expired_token, invalid_device_code | device flow |
| 401 | not_authenticated, invalid_credentials | sign in again |
| 403 | forbidden | no seat, email unverified, and similar |
| 404 | not_found | |
| 409 | device_limit, license_inactive, conflict | |
| 413 | payload_too_large | |
| 422 | validation_error | details lists fields |
| 429 | rate_limited | Retry-After header in seconds |
| 500 | internal_error | quote request_id in support requests |
| 503 | feature_disabled | temporary |

## 6. Rate limits (per 15-minute window)

| Endpoint | Limit |
|---|---|
| /auth/login | 20 per IP; failed attempts per account 10 |
| /auth/login/mfa | 30 per IP; 5 per mfa_token |
| /auth/device/start | 10 per IP |
| /auth/device/token | 300 per IP, plus the per-code interval |
| /licenses/{id}/activations (POST) | 60 per user |
| /entitlements/check-in | 60 per user |
| /licenses/redeem | 10 per user |

On 429, wait Retry-After seconds. Do not retry in a tight loop.

## 7. Network interruption

Use timeouts (connect 10 s, read 20 s). Treat connection errors, 5xx and 503 as
"offline": keep the stored entitlement until exp and retry with backoff (for
example 1, 5 and 15 minutes, then hourly).

Activation and check-in are safe to retry; activation is idempotent per
installation_id.

If a device-token response is lost after approval, the token is gone
(invalid_device_code on the next poll). Start a new device link.

## 8. Backward compatibility rules

- Within /v1 the server only ever adds optional response fields, and only after
  coordination while the contract is frozen. The add-on ignores unknown fields
  and treats an unknown reason as revoked.
- The entitlement v changes only on incompatible payload changes; the add-on
  rejects unknown v. The server will issue the old v for as long as supported
  add-on versions need it.
- The legacy API currently live in production is not compatible with this
  contract. The add-on must target this contract only after cutover.

## 9. Workflows

1. First sign-in: device start → show code and open the browser → poll → store
   the token → GET /v1/licenses.
2. Existing licence: GET /v1/licenses → find the item with usable: true →
   activate or check in.
3. User without a licence: GET /v1/licenses returns [] → show "no licence" with a
   link to the portal (purchase or order-number claim).
4. New computer: generate an installation_id → POST /activations → verify and
   store the entitlement.
5. Device limit reached: 409 device_limit → GET /activations → let the person
   DELETE one (or do it in the portal) → activate again.
6. Licence revoked or refunded: check-in revoked with a reason → delete the
   entitlement → disable features → show the reason.
7. Offline use: verify the stored entitlement locally (section 4.3) at start-up;
   the add-on works until exp; check in again when back online.
8. Expiry: after exp with no successful check-in, disable licensed features and
   ask the person to connect.
9. Sign out: POST /v1/auth/logout → delete the token. Optionally DELETE the
   activation first to free the slot, then delete the entitlement.

Verified end to end over HTTP (isolated stack): workflows 1, 2, 3, 4, 5, 6
(suspended licence) and 9, plus signature verification with the published key.
Workflows 7 and 8 are client-side; the server part (expiry in the payload,
renewal by check-in) is covered by the tests.

## 10. Quick reference for the add-on

| Purpose | Request | Success |
|---|---|---|
| Start sign-in | POST /v1/auth/device/start {"client_name"} | 200 codes and URI |
| Collect token | POST /v1/auth/device/token {"device_code"} | 200 {"status":"authenticated","token","expires_at"} |
| Password sign-in | POST /v1/auth/login {"email","password","token_mode":"bearer"} | 200 token or mfa_required |
| Second factor | POST /v1/auth/login/mfa {"mfa_token","code","token_mode":"bearer"} | 200 token |
| Who am I | GET /v1/auth/me | 200 account |
| Sign out | POST /v1/auth/logout | 204 |
| Licences | GET /v1/licenses | 200 [LicenseOut] |
| Redeem code | POST /v1/licenses/redeem {"code"} | 201 LicenseOut |
| Activate | POST /v1/licenses/{id}/activations {"installation_id","label"} | 200 {"activation","entitlement"} |
| Devices | GET /v1/licenses/{id}/activations | 200 [ActivationOut] |
| Deactivate | DELETE /v1/licenses/{id}/activations/{activation_id} | 204 |
| Renew or check | POST /v1/entitlements/check-in {"entitlement","installation_id"} | 200 {"status","reason","entitlement","license"} |
| Public key | GET /v1/entitlements/public-key | 200 {"key_id","public_key_pem"} |

All of them except the device-flow pair, login and the public key need
`Authorization: Bearer <token>`.
