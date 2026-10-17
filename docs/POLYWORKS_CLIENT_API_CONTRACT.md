# PolyWorks account client — backend handoff

Status: **PROPOSED CLIENT ADAPTER, NOT AN APPROVED PRODUCTION API**.
Target origin supplied by the product owner: `https://api.cloth-next.de`.
No request was made to that origin and no deployment is assumed.

The local add-on repository contains no OpenAPI/authentication/license contract.
Read-only inspection of `Cloth_neXt_Website/services/auth.ts` found a browser demo
using localStorage, not production authentication. `ARCHITECTURE.md` describes
accounts/entitlements conceptually but supplies no wire schemas. No server or
website repository was changed. There is no confirmed API mismatch to report;
the missing contract itself blocks interoperability sign-off.

## Responsibility and readiness

Codex owns Companion EXE, Blender display, offline verifier and client adapter.
Claude owns API, authentication, authoritative Superhive verification, account
database, entitlement issuance, website and infrastructure. No local account
database or signing service exists in this implementation.

All account inputs and changes, including sign-out, are exclusively in the
existing Companion application's new account window. Blender receives a public,
non-authoritative display projection and opens Companion with Manage Account.
No tokens, activation codes, passwords or editable account RNA fields enter
Blender, .blend files, solver configuration, bake IPC or solver binaries.

`PRODUCTION_CONTRACT` is inert. There are **no production endpoint paths,
client IDs, public keys or account browser URLs** in it. Approval cannot come
from a downloaded config, .blend file, user-entered URL or environment override.
An approved release must populate the release-owned contract after agreeing the
following adapter or explicitly revising it. Current tests use `/test/...`
paths and ephemeral keys only; those names are not a server specification.

## Required operations — paths TBD by Claude

|Operation|Proposed request|Required response / semantics|
|---|---|---|
|Registration website|Browser opens approved HTTPS registration URL|Passwords, consent, MFA and registration stay in browser|
|Device authorization|POST form: `client_id`, `scope=account license offline_access`, `installation_id`|`device_code`, `user_code`, `verification_uri`, integer `expires_in` (1–1800), optional integer `interval` (1–60, default 5)|
|Token exchange|POST OAuth form: `client_id`, device-code grant URN and `device_code`; or `grant_type=refresh_token`, `refresh_token`|`access_token`, `token_type=Bearer`, integer `expires_in` (1–86400), `refresh_token` (required initially; optional on refresh)|
|Account profile|GET, Bearer access token|`email` string; backend must derive identity from authenticated session|
|Revoke session|POST OAuth form: `client_id`, `token`, `token_type_hint=refresh_token`|200 object or 204; revoke server refresh session; local sign-out proceeds when server offline|
|Synchronize entitlement|POST authenticated JSON: `installation_id`|`entitlement` compact signed JWS string; installation allocation/limits decided by server|
|Redeem one-time code|POST authenticated JSON: `code`, `installation_id`; `Idempotency-Key` UUID|`status=redeemed`; then client separately fetches and verifies entitlement. Acknowledgement alone never activates a license|
|Verify Superhive purchase|POST authenticated JSON: `product=cloth-next`, `installation_id`, `local_purchase_detected=true`|`status=pending`, `verified` or `unverified`; only `verified` allows entitlement synchronization|
|Activated devices (optional)|GET authenticated, enabled only with explicit capability and route|`devices` array of objects; current UI shows count. Device removal remains a website operation|
|Manage account website|Approved HTTPS browser URL|No access/refresh token or activation code in URL|

Browser sign-in uses a **proposed** OAuth device authorization grant, not an
implemented backend assertion. Confirm RFC 8628 support/public client ID/scopes
with Claude. Client honors pending/slow-down/expiry/denial, polls off the UI
thread and only accepts verification URLs on release-pinned HTTPS origins.
No password entry, client secret or embedded authentication browser is used.

HTTPS certificates use the system Python trust configuration. API redirects are
refused so Bearer tokens/codes cannot be forwarded to another host. Requests use
12-second timeouts and a 1 MiB response limit. No retries automatically redeem a
code. If redemption succeeds but fetching the license fails, the customer must
synchronize their license rather than assume redemption failed. The server must
retain the account's entitlement and make retries/recovery safe, including a
timeout after server-side commit. An expired access token currently prompts a
manual retry/sign-in; no opaque automatic replay of redemption occurs.

## Error contract

Error body proposal: `{ "error": "machine_code" }`; arbitrary server messages
are never displayed or logged as credentials. Required activation codes:
`invalid_code`, `code_redeemed`, `code_expired`, `code_unavailable`.
OAuth: `authorization_pending`, `slow_down`, `access_denied`, `expired_token`,
`invalid_grant`. Other client states: `sign_in_required`,
`verification_pending`, `purchase_not_verified`, `network_unavailable`,
`invalid_response`, `invalid_entitlement`, `not_ready`.
Confirm HTTP status mappings with Claude (401 = sign-in required; service/rate
limits remain unavailable without erasing local state). No server traceback or
user-supplied message is exposed. Rate-limit recovery is manual outside device
polling. Invalid/unknown responses cannot activate licenses.

## Offline entitlement proposal — must be approved before issuance

Compact JWS, Ed25519 signature over exact ASCII `base64url(header).base64url(payload)`.
Unpadded canonical base64url. Protected header has exactly
`{ "alg":"EdDSA", "typ":"JWT", "kid":"<pinned release key id>" }`.
No `jku`, `jwk`, unprotected key, critical extension or algorithm substitution.
Client rejects duplicate JSON members and unknown key IDs. 32-byte trusted raw
Ed25519 public keys live only in the release-owned trust map; **no private keys
ship**. Discuss rotation/overlap, emergency revocation and distribution of new
trust roots before release. Server-provided keys are never auto-trusted.

Required signed claims:

```json
{
  "iss": "<issuer agreed with Claude>",
  "aud": "cloth-next",
  "product": "cloth-next",
  "sub": "<account id>",
  "jti": "<license entitlement id>",
  "tier": "Personal",
  "installation_id": "<client installation UUID>",
  "iat": 1800000000,
  "nbf": 1800000000,
  "exp": 1802592000
}
```

Tier is exactly `Personal` or `Commercial`; these names/product IDs are a
proposal requiring backend agreement. Times are integer Unix seconds. No extra
offline grace period or clock-skew acceptance has been invented. Invalid,
expired, wrong-product, wrong-installation or tampered tokens never replace
the saved entitlement. A valid local token remains usable until its signed
expiry without network access; sign-out does not delete it. An unavailable
crypto provider preserves the file and reports inability to verify it.

The installation UUID is stable per Blender CONFIG account directory, not a
hardware fingerprint. It is not sent until an explicit account operation.
It does not prove ownership of a physical device: server authentication and
device-allocation policy are mandatory. Copying the profile also copies this
binding; confirm whether hardware-backed keys are required before finalizing
the backend model. Wall-clock rollback cannot be securely prevented by an
ordinary local signed token; no claim of tamper-proof offline enforcement is
made. Confirm offline period, perpetual-license semantics and revocation policy.

## Persistence and safety

Companion stores only refresh token + account email in an OS-protected session.
Windows uses user-scope DPAPI plus installation entropy, never machine-wide
encryption. Linux/macOS use explicitly supported OS keyring providers; missing,
plaintext or generic fallback providers fail closed. Access tokens stay in
memory. Signed entitlement and installation UUID are public local artifacts,
written separately; entitlement replacement is atomic and happens only after
successful cryptographic/claim verification. Existing unrelated/legacy license
files are not read, migrated, deleted or gated.

Display cache contains only email, connection/tier/verification labels and optional
devices; Blender treats it as informational. It is **not** authorization proof.
No account state enforces restrictions, interrupts running bakes, changes
solver parameters or contacts the server during a bake. Account network work
has a separate executor and no references to bake transport or solver processes.

## Remaining backend/distribution dependencies

1. Approved OpenAPI paths, exact schemas/error codes and capability negotiation.
2. OAuth public client, browser registration/verification/management URLs,
   origin allowlist, session expiry/rotation/revocation policy and device flow approval.
3. Issuer, approved entitlement claims, pinned public keys and rotation policy.
4. Authoritative authorized Superhive integration; local repository metadata
   remains a hint and no Superhive token is copied or submitted by the client.
5. One-time-code lifecycle, transactional redemption/idempotency, account/device limits.
6. Approved crypto/keyring dependency packaging and distribution permissions
   (current Blender manifest network permission describes solver downloads only).
   The isolated Windows Companion build includes cryptography; Linux OS keyring
   packaging and production bundles still need distribution validation.
7. Staging end-to-end tests using approved backend and fixture entitlements;
   no live account/activation success has been claimed in its absence.
8. Any future enforcement requires separate explicit product/distribution approval;
   this integration deliberately has no solver/bake enforcement hook.

References: [OAuth device grant, RFC 8628](https://www.rfc-editor.org/rfc/rfc8628),
[Ed25519 provider verification](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/),
[Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).
