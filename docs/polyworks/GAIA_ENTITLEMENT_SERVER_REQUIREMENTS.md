# Server requirements: installation-bound GAIA entitlements (for TETHYS)

Status (2026-10-25): binding contract, deployed in production. Public key `k1`
matches the engine. Live client tests passed (issuance, renewal, engine
verdict `valid`, revocation of a test account → token deleted, engine
`missing`). Enforcement is **on** (`kEnforceGaiaEntitlement = true`). The
add-on uses only `POST /v1/account/gaia-entitlement` for this flow (neither
`/v1/entitlements/gaia` nor `/v1/entitlements/check-in`).

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

## 2. Endpoint

`POST /v1/account/gaia-entitlement` — authenticated with the existing bearer
session (device link / login). No new registration step.

Request: `{"installation_id": "inst_…"}` (the client's existing installation id,
32 URL-safe characters after `inst_`; validate the same way as activations).

Responses (contract error envelope as elsewhere):

| Case | Response |
|---|---|
| GAIA decision `granted` | `200 {"status":"granted","mode":…,"entitlement":"<token>"}` |
| `pending`, `declined`, none | `200 {"status":"pending"/"declined"/null,"mode":…,"entitlement":null}` |
| Account suspended/disabled | `403 account_suspended` (or `account_disabled`) |
| Not signed in / session ended | `401 not_authenticated` |
| Rate limit | `429 rate_limited` with `Retry-After` |

The same call issues and renews. The client deletes its stored GAIA
entitlement on every non-granted answer and on `401 not_authenticated`,
`403 account_suspended` / `account_disabled` / `gaia_access_required`. It keeps
it on network errors, 5xx, 429 and `404` (endpoint not yet deployed), but still
deletes a kept token once its `exp` has passed.

Suggested limit: 30 requests per 15 minutes per account (the add-on asks only
when it checks GAIA access: Blender start, Check Access, access request,
invite redemption, GAIA install).

## 3. Token

Same format and signing key as the licence entitlement
(`base64url(payload) "." base64url(ed25519(payload))`, key `k1`, see
`BLENDER_ADDON_API_CONTRACT.md` §4.1–4.2). Payload (compact JSON, no
duplicate keys):

```json
{"typ":"polyworks.feature-entitlement","v":1,"kid":"k1",
 "sub":"<account id>","inst":"<installation id from the request>",
 "prd":"cloth-next","feat":["gaia"],"src":"grant|request|invite|tester|everyone",
 "iat":1761400000,"exp":1762004800}
```

* `typ` must differ from `polyworks.entitlement`; a licence entitlement is
  never accepted as GAIA proof (the engine checks `typ`). `prd` is mandatory.
  There is no `purpose` claim. `kid` is `k1` (the only key in shipped builds).
* `exp - iat` = **7 days** (604,800 s). Each call issues a fresh token.
* `src` is informational (support), not checked by the engine.
* Never include e-mail addresses or licence data.

## 4. Revocation and offline validity

There is no online check inside the engine. After GAIA access is revoked or the
account is suspended:

* the next GAIA access check of the add-on deletes the stored token
  (`removed`), immediately ending TETHYS on that computer once enforcement
  is on;
* a computer that stays offline keeps a token that was already issued until its
  `exp`: **at most 7 days plus 5 minutes clock tolerance** after the last
  successful issuance. That is the documented worst case.

## 5. Engine checks (already implemented)

Signature with a compiled-in key (`k1` only in shipped builds), `kid`, `typ`,
`v == 1`, `prd == "cloth-next"`, `inst` equals the local
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
