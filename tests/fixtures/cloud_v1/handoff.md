# Cloud Bakes add-on handoff — 2026-10-24 (owner pilot)

Relayed by Tim from Codex on 2026-10-24; replaces the Phase-0 handoff.
Contract files beside it: `openapi.json` (= cloud-bakes-openapi-v1.json),
`examples.json` (= cloud-bakes-examples-v1.json), `cloud-bakes-api-v1.md`,
downloaded from https://account.cloth-next.de/assets/.

- API origin https://api.cloth-next.de, base /v1/cloud; dashboard
  https://account.cloth-next.de/dashboard/cloud-bakes. Existing PolyWorks
  bearer session only, never the release token.
- QuoteOut: gpu_type_id, usage_percent (A5000 290 = one credit lasts 2.9 s of
  Pod runtime), maximum (integer credits), unit "credit",
  max_execution_seconds, expires_at. The accepted quote is a ceiling, not the
  final debit. Use the server-selected GPU.
- JobOut: gpu_type_id (nullable) and billing (nullable)
  {"unit":"credit","reserved":311,"charged":null,"finalized":false}; after
  settlement {"reserved":0,"charged":98,"finalized":true}. Use billing.charged
  only when finalized; a settled 0 is a real zero charge. statistics.elapsed_ms
  is the finalized accounting duration (provider run including start-up).
- result_expires_at / result_available keep the seven-day result window.
- Owner pilot debits the real account credit ledger; external payment
  charging stays disabled. Automatic add-on-triggered Pod dispatch and public
  API routing remain server-side dependencies: a submitted job is not proof of
  a Pod launch.
