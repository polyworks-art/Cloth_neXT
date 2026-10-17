# Codex review handoff: PolyWorks API integration

* Branch: `claude/polyworks-api-integration-l9axft` (from `main` at `3a4b632`)
* Implementation commit: `5e9104a` (this document is added in the following
  commit; review `git diff 3a4b632..HEAD`)
* Source of truth: frozen contract v1, client copy at
  `docs/polyworks/BLENDER_ADDON_API_CONTRACT.md`. Full description:
  `docs/POLYWORKS_API_INTEGRATION.md`.
* Not released, not merged, no production change, `api.cloth-next.de` never
  contacted.

## Integration architecture (one paragraph)

The Companion EXE (`companion/app.py --mode account | account-sync`) is the
only HTTP client of the PolyWorks API (`cloth_next/polyworks/api.py`,
`service.py`). It keeps the bearer token in a private vault (`vault.py`) and
publishes public files in a per-OS-user folder: `installation-id`,
`entitlement` (Ed25519-signed by the server), `state.json` (allowlisted display
state) and `companion.json` (presence). Blender
(`cloth_next/blender/account_status.py`) polls those files with a timer,
reads/verifies them on a worker thread, and draws a read-only summary in
Preferences. Blender's "authorized" is derived only from an entitlement it
verified itself with pinned keys.

## Security assumptions

1. The same OS user who runs Blender is trusted with their own files; other
   users are not (POSIX ownership/mode checks; Windows `%LOCALAPPDATA%` ACL).
   A same-user process can always tamper with the local machine; it still
   cannot forge an entitlement without the server's private key.
2. Pinned public keys in `polyworks/trust.py` are the trust root. Runtime keys
   from the API are never trusted. `CLOTH_NEXT_POLYWORKS_TRUST_FILE` adds
   development keys only; they cannot shadow a pinned `kid` and every view built
   on them carries a visible warning.
3. HTTPS with the system trust store; plain HTTP only for loopback test
   backends; no redirects followed; server browser URLs restricted to the API
   or portal origin.
4. Offline revocation is impossible by construction: a revoked entitlement
   stays valid until `exp` (≤ 30 days) on a machine that never checks in.
5. `PRODUCTION_KEYS` is intentionally empty until the product owner supplies
   the production key, so production entitlements currently fail closed.

## Key source files

| File | Role |
|---|---|
| `cloth_next/polyworks/api.py` | Contract HTTP client, error mapping, response validation |
| `cloth_next/polyworks/service.py` | Workflows 1–9, backoff, session death, sign-out |
| `cloth_next/polyworks/entitlements.py` | Contract 4.3 verification |
| `cloth_next/polyworks/ed25519.py` | RFC 8032 verify (pure Python) + `cryptography` provider |
| `cloth_next/polyworks/trust.py` | Pinned keys, development trust file |
| `cloth_next/polyworks/storage.py` | Account root, atomic writes, trusted reads, lock |
| `cloth_next/polyworks/vault.py` | DPAPI / keyring / user-only file session vault |
| `cloth_next/polyworks/state.py` | Allowlisted public state schema |
| `cloth_next/polyworks/status.py` | The single status state machine |
| `companion/account_window.py` | Interactive UI and headless sync entry points |
| `cloth_next/blender/account_status.py` | Blender timer, worker, launcher, renewal trigger |
| `cloth_next/blender/preferences.py` | `draw_polyworks_status` (read-only) |
| `tests/polyworks_backend.py` | Isolated contract test backend |

## Test commands

```bash
pip install -r requirements-build.txt          # includes cryptography==46.0.3
xvfb-run -a pytest -q                           # full suite (Tk tests need a display)
pytest -q tests/test_polyworks_*.py             # integration-specific suites
python tools/validate_extension.py cloth_next --phase source
python companion/build_companion.py && xvfb-run -a python tools/verify_companion.py
blender --background --factory-startup --python-exit-code 1 \
  --python tools/blender_polyworks_status_smoke.py -- --source
```

Against a real isolated backend (never production), set for the Companion and
Blender processes: `CLOTH_NEXT_POLYWORKS_API_URL=http://127.0.0.1:8000`,
`CLOTH_NEXT_ACCOUNT_ROOT=<temp dir>`, and `CLOTH_NEXT_POLYWORKS_TRUST_FILE`
pointing to `{"keys":[<GET /v1/entitlements/public-key response>]}`.

Recorded results are in `docs/POLYWORKS_API_INTEGRATION.md` (Test results).

## Important implementation decisions

1. **File-based Companion → Blender handoff** instead of the Bake socket: the
   socket is Blender-owned and per Bake; account state must survive either side
   restarting and serve several Blenders.
2. **Per-OS-user account root**, not Blender's versioned CONFIG folder, so a
   Blender upgrade or Stable/Beta/Dev switch keeps the same installation id and
   device slot (contract 3.3).
3. **Activation is explicit** (button), never automatic after sign-in, because
   it consumes a device slot.
4. **Sign-out asks** whether to deactivate this computer (contract workflow 9
   "optionally"). Without deactivation the offline entitlement stays until `exp`.
5. **Blender launcher button** "Open Cloth NeXt Companion" is the only control
   in the account UI; it runs no account action.
6. **Headless renewal** is started by Blender only when due, signed in, no Bake
   active, not background, no Companion window open; spacing ≥ 10 minutes.
7. **Password + MFA** is offered in the Companion as a secondary path (contract
   2.2); the device link is primary.
8. **No feature gating.** `authorized()` is provided but unused (contract 4.3a:
   the licence covers PolyWorks services; never interrupt a Bake).
9. `prd == "cloth-next"` is checked in addition to the contract's four checks.
10. The GAIA owner-branch account window was ported (look and structure kept);
    its proposed OAuth/JWS client was replaced by the frozen contract.

## Potential regression risks

* `cloth_next/blender/registration.py`: new register step and class. Covered by
  the existing registration tests and the new reload/timer tests.
* `cloth_next/blender/preferences.py`: new box drawn above "Learn Cloth NeXt".
  Draw runs only in-memory code; failure modes are attribute errors on an
  unexpected view (none known).
* `companion/app.py`: new `--mode` values and `--blender-version`,
  `--purchase-detected`; Bake/information argument validation unchanged.
  `verify_companion.py` exercises all modes on the built EXE.
* `requirements-build.txt` adds `cryptography==46.0.3`; Windows PyInstaller
  build must bundle it (hidden import added) — not built on Windows here.
* Timer: one persistent 2 s timer; a stat of four files per tick.

## Unresolved issues

1. Production public key (`key_id` + PEM) not yet provided.
2. OpenAPI snapshot not compared; no run against the real isolated backend.
3. Windows: DPAPI vault, Windows EXE build, `%LOCALAPPDATA%` behaviour untested
   here. macOS: Keychain path untested (no macOS build target exists).
4. Manifest network-permission wording, Superhive "Purchase validated" wording.
5. Merge conflict expected with `codex/gaia-owner-2.9.26`
   (`companion/account_window.py`, `companion/app.py`, `onboarding_window.py`
   account buttons on that branch).

## Recommended final Blender stability tests

1. Windows, real Blender 5.0/5.1: enable, disable, re-enable the extension three
   times; confirm `bpy.app.timers` holds one `_tick` and no
   `ClothNeXtAccountStatus` thread survives disable.
2. Open Preferences > Add-ons > Cloth NeXt while the Companion signs in,
   activates, deactivates and signs out; the panel must update within ~2 s and
   never freeze.
3. Start Blender with the Companion closed, then open it; start the Companion
   first, then Blender; quit the Companion during a status read.
4. Two Blender instances open at once; both reflect changes; only one
   `--mode account-sync` runs at a time.
5. Start a Bake, force `next_check_at` into the past: no sync process may start
   until the Bake ends; disconnect the network during a Bake: the Bake finishes.
6. Disconnect the network: status becomes "Offline – license verified"; set the
   clock past `exp`: "License expired"; set it before `iat`: "could not be
   verified"; no data or cache is touched in any case.
7. Run the existing cloth/collider/water smoke scripts to confirm no simulation
   regression (`tools/blender_*_smoke.py`).
8. Inspect `%LOCALAPPDATA%\PolyWorks\ClothNeXt`: no token in any plain file,
   `session.dpapi` only ciphertext; check Blender logs and crash reports for
   tokens.
