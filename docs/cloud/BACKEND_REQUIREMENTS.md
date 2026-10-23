# Cloud Bakes: was das Add-on vom Backend braucht (Stand Phase 0)

Stand 23.10.2026, Branch `claude/project-thread-fd9edx`. Ergänzt Abschnitt 15 des
Konzepts (B1–B17) um die Befunde aus Phase 0. Kein Vertragsentwurf: Form und Pfade
unter `/v1/cloud/` legt das Backend fest. Das Add-on spricht ausschließlich über
`cloth_next/cloud/transport.py` (`CloudBackend`); eine HTTP-Umsetzung wird auf den
fertigen Vertrag abgebildet, ohne die Aufrufer zu ändern.

## 1. Was ein Paket enthält (Server muss alles davon ausführen können)

| Rolle | Datei | Zweck auf dem Server |
|---|---|---|
| `data` | Scene-Payload | `upload_atomic`-Daten wie lokal |
| `param` | Param-Payload | bei Native Flow bereits mit kodierter Fluid-Sequenz |
| `official-bindings` | JSON | Nähte, Face-Friction, Material Zones. **Muss nach dem BUILD mit dem Python der Solver-Installation angewendet werden** (`ppf/official_scene_worker.py`), wie lokal in `SolverSession._apply_official_bindings`. |
| `hydro-model` | JSON | Hydrodynamic Pressure: Modell; Pfad als `PPF_EXPERIMENTAL_HYDRO_TEST_JSON` |
| `hydro-volume` | viele Dateien `hydro/<rel>` | Hydrodynamic Pressure: Volumen; Ordner als `PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR`, relative Struktur erhalten |
| `snapshot`, `snapshot-arrays` | JSON + Binär | nur aufbewahren (B17), nicht zum Rechnen nötig |

Befund 1: Das Konzept nannte nur `data`/`param`/`fluid`. Für GAIA sind die
`official-bindings` immer da und Pflicht.

Befund 2: Water Flow mit Hydrodynamic Pressure (inzwischen Standard) läuft nicht
über die native Fluid-Sequenz, sondern über vorbereitete Volumen pro Frame. Die
Vorbereitung läuft lokal (Python der GAIA-Installation, liest den FLIP-Cache);
in die Cloud gehen nur die Volumen. Der FLIP-Cache selbst wird nie übertragen.
Die Datei `hydrodynamics.json` wird mit bereinigter Provenienz verpackt (der
lokale FLIP-Pfad wird entfernt). Größe: siehe Phase-0-Bericht.

Befund 3: Der alte Held-Stream-Modus (GAIA-Protokoll 0.23) braucht einen lokalen
Helfer während des Laufs und ist für die Cloud nicht vorgesehen; das Paketieren
lehnt ihn mit `water_mode_unsupported` ab.

## 2. Manifest

`kind = cloth-next.cloud.package`, `package_schema = 1`, `client_job_id`,
`display_name`, Add-on- und Blender-Version, `solver` (Engine, `build_id`,
Protokoll, Schema), `frames` (start, end, count, fps), `water`
(`none`/`native-fluid`/`hydrodynamic`), `objects` (uuid, Typ, Vertex-Anzahl,
Solver-Weltmatrix), Fingerprints, Payload-Hashes, `snapshot_digest`, `files`
(Rolle, Name, SHA-256 und Größe der unkomprimierten Bytes). Kanonisches JSON;
sein SHA-256 ist die Paket-ID. Keine Pfade, keine Secrets (Scan beim Erzeugen).

Hinweis zu P4: Objektnamen stehen bereits im Scene-Payload `data` (so verlangt
es der Solver). Das Manifest selbst enthält nur UUIDs und den Jobnamen.

## 3. Vom Add-on vorausgesetztes Verhalten (zusätzlich zu B1–B17)

- **Idempotenz**: `create` mit Schlüssel = `client_job_id`, `submit` mit
  `<client_job_id>:submit`, `cancel` mit `<client_job_id>:cancel`. Gleicher
  Schlüssel + gleicher Inhalt → gleiche Antwort; anderer Inhalt →
  `idempotency_conflict`. Suche per `client_job_id` (B16).
- **Upload je Datei**: Status (`received`, `confirmed`), Block-Upload an einen
  Offset (wiederholter Block am selben Offset wird ignoriert), Abschluss mit
  serverseitiger SHA-256-Prüfung. Dedup über den Status: bereits bestätigte
  Hashes des Kontos werden nicht erneut gesendet; strikt pro Konto (B5).
- **Manifest** kommt im `create`-Request mit und wird nicht separat hochgeladen.
- **Submit** schlägt mit `upload_incomplete` fehl, solange eine Datei fehlt.
- **Ergebnis** (B10): Vertex-Map, Surface-Map (nur bei SOLID), Frame-Blöcke mit
  aufeinanderfolgenden `vert_<N>.bin` (das Add-on nutzt `frames-NNNNNN-MMMMMM.bin`
  mit 25 Frames je Block), je Datei SHA-256 und Größe, `build_id` des
  tatsächlich ausgeführten Builds, `snapshot_digest`, Anzahl lückenlos fertiger
  Frames und Teilergebnis-Markierung. Download per Bereich (Offset/Länge).
- **Snapshot-Abruf**: Paketdateien eines Jobs müssen für den Besitzer abrufbar
  bleiben (Snapshot-Wiederherstellung, Konzept 6.3 Punkt 5).
- **Fehlercodes**: das Add-on kennt die Liste aus B14 plus `session_expired`,
  `network_unavailable`, `service_unavailable`, `invalid_response`; alles andere
  wird `CNX-E200` (Fehlertexte des Servers werden nie angezeigt, nur `request_id`).

## 4. Server-Ausführung (Referenz)

`tools/cloud_phase0.py` enthält mit `real_executor` die Referenz, wie ein Server
ein Paket ausführt: `SessionScene` aus Manifest + Dateien bauen (Objektname =
UUID), `official_bridge_json` aus `official-bindings`, Volumen in einen Ordner
schreiben und als Umgebung übergeben, `SolverSession(...).run()` mit
`raw_output_sink`; die Rohdateien werden unverändert zurückgegeben.

## 5. Offen

- Konkreter Vertrag `/v1/cloud/` (Pfade, Felder, Auth) – Integration im Add-on
  ist bis dahin nur gegen den Testserver geprüft.
- Linux-Builds und Zuordnung Server-Build ↔ lokale `build_id` (B2).
- Angebot/Kostenobergrenze (B3), Kontingent, Aufbewahrung (P1–P3).

## 6. Abgleich mit Vertrag v1 (Pilot, 23.10.2026) – Änderungswünsche

OpenAPI/Beispiele lagen beim Abgleich nicht vor; Grundlage ist die Markdown-Beschreibung.

| # | Vertrag v1 | Bedarf aus Phase 0 | Wunsch |
|---|---|---|---|
| C1 | `files`: genau `data`, `param`, `import-snapshot`, `fluid` | GAIA/AETHER 0.26 brauchen `official-bindings` (Nähte, Face-Friction, Material Zones), nach BUILD mit dem Solver-Python angewendet | Rolle `official-bindings` aufnehmen (Pflicht bei Protokoll 0.26) |
| C2 | `fluid` genau dann, wenn Water Flow | Hydrodynamic Pressure: `hydro-model` + viele `hydro-volume`-Dateien (≈ 12 MB/Frame, relative Pfade, zwei Umgebungsvariablen); Native Flow: Fluid steckt bereits in `param` | Rollen `hydro-model`/`hydro-volume`; `fluid` nicht verlangen |
| C3 | eine Upload-Session pro Rolle | mehrere Dateien je Rolle (`hydro-volume`) | Upload-Session pro Datei: Rolle + relativer Name (validiert, keine absoluten Pfade, kein `..`) |
| C4 | Teilergebnisse aus | – | ok; E18 bleibt offen |
| C5 | Build-Zuordnung: Desktop-`build_id` **und** Artefakt-Hashes | Add-on kennt `gaia-build.json` (`build_id`, Datei-Hashes) bzw. AETHER-Release-Tag | Feldliste in der OpenAPI benötigt; welche Hashes genau? |
| C6 | Submit verlangt `quote_id` + `accepted_maximum` | Bestätigungsdialog mit Server-Angebot | passt; Add-on zeigt Test-Einheiten nur als solche |
| C7 | Server berechnet `manifest_sha256` | Add-on hatte eigenen Hash | Add-on speichert den Server-Wert (umgestellt) |
| C8 | `import_snapshot_digest` = Datei-Hash | Snapshot war JSON + Binär | Add-on liefert jetzt eine Datei, Digest = Datei-Hash (umgestellt) |
| C9 | Ergebnis über `result-session` + `results/{sha256}?t=` | Add-on lädt Dateien bereichsweise mit Hash | passt; erlaubter Download-Host muss feststehen |
| C10 | Zustände `starting`, `cancelling` | – | Add-on bildet sie auf „Running“/„Cancelling“ ab |
