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

## Retry (Tim, 24 October 2026)

`POST /v1/cloud/jobs/{job_id}/retry`, body `{"accepted_maximum": <credits>}` →
`JobOut` of the retry job. Only a FAILED job whose error is a free infrastructure
failure (`provisioning_failed`, `worker_lost`, `active_pod_exists`) with no used
credits and a retained package can be retried; the retry job's client_job_id is
`uuid5(client_job_id, "cloud-bake-retry")`, a repeated call returns the same job,
and `accepted_maximum` must equal a current quote of that manifest. `JobOut`
carries `retryable`, `retry_of`, `retried_as`. The published OpenAPI asset does not
list these yet; the fixture adds them from the live JobOut and the server schema.
