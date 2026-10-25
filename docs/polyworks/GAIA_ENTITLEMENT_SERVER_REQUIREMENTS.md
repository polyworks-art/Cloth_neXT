# Server requirements: installation-bound GAIA entitlements (for TETHYS)

Status: server issues GAIA entitlements (`POST /v1/entitlements/gaia`,
renewal via check-in). Client and engine are adapted to that format. The
native engine verifies the token but does **not** enforce it
(`kEnforceGaiaEntitlement = false`) until the joint test with all existing
GAIA testers has passed.

## Purpose

TETHYS (native wetness engine bundled with Cloth NeXt) is available only to
accounts with GAIA access. The engine must verify that itself, offline, from a
token signed by the server. GAIA access is the **only** source: no Superhive
purchase, Cloth NeXt licence, seat, device activation or TETHYS activation is
required or considered.

## 1. Before implementing: confirm the production access policy

Do not change who has GAIA access. Record, from production:

1. The active GAIA mode (`selected`, `everyone`, `none`).
2. Every account whose `GET /v1/account/gaia-access` currently answers
   `granted`, grouped by path: administrator grant, approved access request,
   redeemed invite, dev-tester list, `everyone` mode.
3. Which of those accounts have **no** Cloth NeXt licence and **no** device
   activation (they must still receive GAIA entitlements).
4. Suspended or disabled accounts (must receive nothing).

The issuance rule below must reuse the exact function that answers
`gaia-access` today, so the set of entitled accounts equals the set of
`granted` accounts by construction.

## 2. Endpoints (as deployed 2026-10-25)

* **Issue:** `POST /v1/entitlements/gaia` with the existing bearer session,
  body `{"installation_id": "inst_…"}` → `200 {"entitlement": "<token>"}`;
  `403 gaia_access_required` without GAIA access, `403 account_suspended`
  for suspended accounts. No new registration step.
* **Renew / revocation check:** `POST /v1/entitlements/check-in` with
  `{"entitlement": <stored GAIA token>, "installation_id"}` →
  `{"status":"valid","entitlement":<fresh token>}` or `revoked`/`invalid`.

Client behaviour (`AccountService.refresh_gaia_entitlement`, run at every
GAIA access check): with a stored token → check-in; without → issue.
`revoked`/`invalid`, `gaia_access_required`, `account_suspended` delete the
stored token. Network errors and 5xx keep it; `404 not_found` (server without
the endpoint) changes nothing.

## 3. Token (as issued by the server)

`base64url(JSON) "." base64url(Ed25519 signature)`, key `k1`:

```json
{"typ":"polyworks.entitlement","v":1,"purpose":"gaia-access","feat":["gaia"],
 "kid":"k1","sub":"<account>","inst":"<installation id>","iat":…,"exp":…}
```

The engine and the add-on require `typ == "polyworks.entitlement"`, `v == 1`,
`purpose == "gaia-access"`, `"gaia"` in `feat`, `inst` equal to the local
installation id and the validity window. `prd` is optional; when present it
must be `"cloth-next"`. A licence entitlement (same `typ`, no `purpose`) is
refused.

## 4. Revocation and offline validity

There is no online check inside the engine. After GAIA access is revoked or the
account is suspended:

* the next GAIA access check of the add-on deletes the stored token
  (`removed`), immediately ending TETHYS on that computer once enforcement
  is on;
* a computer that stays offline keeps a token that was already issued until its
  `exp` (+5 minutes clock tolerance). With the 7-day lifetime proposed here that
  is at most 7 days after the last successful issuance; the actual bound is the
  server's `exp - iat`.

## 5. Engine checks (already implemented)

Signature with a compiled-in key (`k1` only in shipped builds), `kid`, `typ`,
`v == 1`, `purpose == "gaia-access"`, optional `prd == "cloth-next"`, `inst` equals the local
`installation-id` file, `"gaia"` in `feat`, `iat - 300 <= now <= exp + 300`.
The engine reads `installation-id` and `gaia-entitlement` itself from the
PolyWorks account folder (`%LOCALAPPDATA%\PolyWorks\ClothNeXt`, Linux
`~/.config/polyworks/cloth-next`, or `CLOTH_NEXT_ACCOUNT_ROOT`).

## 6. Proof before switching enforcement on

1. Deploy the endpoint. Dry run on production: for every account from §1.2
   call the issuance function with a synthetic installation id and confirm a
   token is produced; for every account from §1.4 confirm none is.
2. Ship an add-on build with the bundled engine (enforcement still off). Its
   start event reports `gaia_entitlement` per bake (stored in the wetness
   sidecar). Confirm `valid` on test machines for each access path, including
   a tester without licence or activation.
3. Only then rebuild the engine with `kEnforceGaiaEntitlement = true`.
   Python cannot switch it.

Contract tests against the test server: `tests/test_gaia_entitlement.py`
(all access paths, accounts without licence/activation, renewal, revocation,
suspension, missing endpoint, offline, installation binding, licence token
refused, production engine trusts only `k1`).
