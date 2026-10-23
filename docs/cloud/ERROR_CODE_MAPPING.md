# Cloud API v1 → Cloth NeXt Fehlercodes

Stand 23.10.2026, Quelle: `cloth_next/cloud/errors.py` (Zuordnung) und
`cloth_next/core/error_codes.py` (Texte, `docs/ERROR_CODES.md`). Das Add-on zeigt nur den
Text der CNX-Zeile und optional die `request_id`, nie die `message` des Servers.
Unbekannte Codes werden `CNX-E200`.

## Envelope-Codes (`{error:{code,…}}`)

| HTTP | v1-Code | CNX | Bedeutung im Add-on |
|---|---|---|---|
| 400 | `bad_request`, `invalid_chunk`, `invalid_file_role`, `invalid_storage_path`, `idempotency_key_required` | E207 | Paket/Anfrage ungültig (Add-on-Fehler oder nicht unterstützte Funktion) |
| 400 | `integrity_mismatch` | E213 | Hash/Größe beim Abschluss falsch → Datei neu hochladen |
| 400 | `water_mode_unsupported` | E207 | Water-Modus nicht Cloud-fähig (0.23 Held-Stream) |
| 401 | `not_authenticated`, `session_expired` | E202 | Neu anmelden; Jobs und Ergebnisse bleiben |
| 403 | `forbidden`, `insufficient_scope` | E203 | Kein Cloud-Zugang für dieses Konto |
| 404 | `not_found` | E200 | Job fehlt/fremd; beim Abgleich per `client_job_id` = „nichts angelegt“ |
| 409 | `idempotency_conflict` | E217 | Gleicher Schlüssel, anderer Inhalt; nie still neue ID |
| 409 | `engine_build_unavailable` | E206 | Build nicht gemappt; kein Wechsel auf anderen Build |
| 409 | `invalid_quote` | E205 | Angebot ungültig/abgelaufen → neues Angebot, neue Bestätigung |
| 409 | `upload_incomplete` | E208 | Upload fortsetzen |
| 409 | `offset_mismatch` | – | intern: Status holen, am Server-Offset weiter |
| 409 | `content_corrupt` | E213 | Inhalt beschädigt |
| 409 | `invalid_state`, `conflict` | E200 | unerwarteter Zustand |
| 410 | `upload_expired` | E208 | intern: neue Upload-Session öffnen |
| 410 | `result_unavailable` | E212 | Ergebnis abgelaufen/nicht verfügbar |
| 413 | `payload_too_large`, `package_too_large` | E207 | Größengrenze |
| 415 | `invalid_content_type` | E207 | Add-on-Fehler |
| 422 | `validation_error` | E207 | Add-on-Fehler (Schema) |
| 429 | `rate_limited` | E218 | automatisch erneut (Retry-After) |
| 429 | `account_limit` | E204 | Konto-Kontingent |
| 503 | `feature_disabled` | E203 | Cloud aus (Pilot/RunPod nicht konfiguriert) |

## Ausführungscodes eines beendeten Jobs (`error_code`)

| v1 | CNX |
|---|---|
| `worker_failed`, `worker_lost`, `native_solver_failed`, `native_process_terminated`, `result_storage_failed` | E210 (Solver/Server-Fehler) |
| `test_limit_reached` | E204 (Obergrenze erreicht) |

## Transport (kein Server-Code)

| Lage | Behandlung | CNX |
|---|---|---|
| Verbindung nicht aufgebaut | `network_unavailable`, nichts gesendet | E201 |
| Gesendet, keine Antwort / Reset / 5xx ohne Envelope (bei POST/PATCH) | `CloudUnconfirmed` → Abgleich per `client_job_id` | E209 |
| Antwort zu groß, kein JSON, falsche Felder, Redirect, fremde Download-URL | `invalid_response` | E200 |
| Upload pausiert (Abbruch/Netz) | fortsetzbar | E208 |
| Lokale Paketdatei fehlt/beschädigt | | E214 |
| Ergebnis passt nicht zum Mesh | | E215 |
| Zu wenig Speicherplatz | | E216 |
