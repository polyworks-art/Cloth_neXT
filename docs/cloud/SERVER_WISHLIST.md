# Cloud Bakes: Was das Add-on vom Server braucht (Änderungen an Vertrag v1)

Stand 23.10.2026. Grundlage: echte Phase-0-Läufe (GAIA/AETHER, Nähte, Soft Body, Water Flow).

## Muss (sonst laufen echte Szenen falsch oder gar nicht)

1. **Datei-Rolle `official-bindings`** (JSON, Pflicht bei Protokoll 0.26, auch AETHER).
   Der Server wendet sie nach dem BUILD mit dem Python der Solver-Installation an
   (`ppf/official_scene_worker.py --project-name … --project-root … --bindings <datei>`).
   Ohne sie fehlen Nähte, Face-Friction und Material Zones.
2. **Water Flow mit Hydrodynamic Pressure**: Rollen `hydro-model` (eine JSON-Datei) und
   `hydro-volume` (viele Dateien, ca. 12 MB pro Frame). Der Server legt sie als Ordner mit
   den gelieferten relativen Pfaden ab und startet den Solver mit
   `PPF_EXPERIMENTAL_HYDRO_TEST_JSON=<model>` und `PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR=<ordner>`.
3. **`fluid` nicht verlangen.** Bei Native Flow steckt die Fluid-Sequenz bereits im `param`;
   eine separate `fluid`-Datei gibt es nicht. `water_flow=true` darf also ohne `fluid` kommen.
4. **Eine Upload-Session pro Datei**, nicht pro Rolle: `UploadIn {role, name}` mit relativem
   Namen (nur `[A-Za-z0-9._/-]`, kein absoluter Pfad, kein `..`). Nötig für `hydro-volume`.
   Das `files`-Map im Manifest entsprechend als Liste `{role, name, size_bytes, sha256}`.
5. **Build-Zuordnung konkret festlegen:** Welche Felder und Hashes schickt das Add-on genau?
   Vorschlag GAIA: `build_id` aus `gaia-build.json` + SHA-256 von `ppf-cts-server.exe` und
   Worker; AETHER: offizieller Release-Tag + dieselben zwei Hashes. Der Server antwortet in
   `capabilities` mit den angebotenen `build_id`s; das Ergebnis meldet die ausgeführte.

## Soll (für den MVP-Ablauf)

6. **OpenAPI und Beispiele** (`CLOUD_BAKES_OPENAPI_V1.json`, `CLOUD_BAKES_EXAMPLES_V1.json`)
   ins Repo oder in die Projektdateien, damit der HTTP-Client exakt danach gebaut wird.
7. **Erlaubte Hosts**: API-Host und Download-Host für `results/{sha256}?t=` festlegen
   (Add-on erlaubt nur diese, nur HTTPS, keine fremden Redirects).
8. **Paketdateien eines Jobs abrufbar** für den Besitzer (mindestens `import-snapshot`),
   solange das Ergebnis aufbewahrt wird – für Import auf einem anderen Rechner ohne `.blend`.
9. **Ergebnis-Format bestätigen**: Vertex-Map, Surface-Map (nur bei SOLID), Frame-Blöcke mit
   aufeinanderfolgenden `vert_<N>.bin`-Bytes und inklusivem erstem/letztem Frame, je Datei
   SHA-256 + Größe, `build_id` der Ausführung, `import_snapshot_digest`.
10. **Transport-Kompression zstd Stufe 1** für Uploads erlauben (Hash bleibt über die
    Rohbytes). Gemessen: Wasser-Upload bei 50 Mbit/s netto −52 %, `data` −75 bis −91 %.
    Rohframes ohne Kompression.

## Kann (später)

11. Teilergebnisse (E18), sobald die Frame-Validierung nativ belegt ist.
12. Linux-Server-Builds und belegte Zuordnung Desktop-Build ↔ Server-Build (E14-Vergleich).
13. Kontingent/Guthaben in `/account`, sobald es ein Abrechnungsmodell gibt (E25).

Bereits passend im Vertrag: Idempotenz, Suche per `client_job_id`, Upload mit Offset und
Chunk-SHA, Dedup über `reused`, Angebot mit `accepted_maximum`, Zustände, Fehler-Envelope,
Abruf ohne Entitlement.
