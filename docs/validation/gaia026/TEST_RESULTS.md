# Testresultate — 13. Oktober 2026

Der reguläre GAIA-0.26-Fluidpfad besteht alle Sequenzstufen. Auch die abschließende 250-Frame-Wiederholung besteht: **technisches GO im dokumentierten Prüfumfang**, keine Veröffentlichung oder automatische Aktivierung.

| Prüfung | Ergebnis | Umfang / Evidenz |
|---|---|---|
| Vollständige Pytest-Suite | 2211 passed, 0 failed | 20 skipped, 3 built_artifact deselected; `production/unit-tests.txt` |
| Reguläre Fluid-/Dry-Sequenzen | PASS | 3, 24, 60, 120, 250 Frames; `production/long-sequence-report.json` |
| 250-Frame-Wiederholung mit Cache-Reuse | PASS | Korrigierter Prozessmodus, vollständige Ressourcenmessung; `production/250-confirm-report.json` |
| PC2 und Blender-Playback | PASS | Alle Samples endlich, Fehler 0,0; separate Dry-Kontrolle je Stufe |
| Cache-Integrität | PASS | Wiederverwendung unverändert, korrumpierte eigene Kopie abgelehnt, gutes PC2 erhalten |
| Dry-Cancel/Resume | PASS | Neuer Blender-Prozess; `production/dry-cancel.json`, `dry-resume.json` |
| Fluid-Cancel/Resume | PASS | 24 Frames; `production/fluid-cancel.json`, `fluid-resume.json` |
| Unerwarteter Server-Abbruch/Resume | PASS | Verifizierte Checkpoints, sechs gültige Prefix-Frames, anschließend 24 Frames fertig; `production/fluid-server-loss-*.json` |
| Regulärer Windows-Lifecycle | PASS | Bake/Rebake/Clear/Save/Reload; `production/regular-operator-lifecycle.json` und `lifecycle-report.json` |
| Runtime-Wechsel | PASS | 0.26 → 0.23 → 0.22 → 0.26; Registry und installierte Runtime-Dateien unverändert |
| Separat konfigurierte Integrationstests | 13 eindeutige Tests bestanden | Erstbatch 10 passed / 3 failed; Cleanup und Stitch anschließend korrigiert und erneut bestanden |
| Drei native Stitch-Varianten | PASS | Mit gleichzeitig laufendem, namensgleich erkanntem Sibling-Prozess; `production/scoped-sewing-with-live-sibling-probe.log` |
| Collider-Attachments | PASS | Statisch, rigid animiert, deformierend animiert; `production/collider-attachments-report.json` |
| Idle / Keepalive | PASS | Tatsächlicher 3-s-Testtimer, Pings über 8,266 s und regulärer Solve; `production/idle-keepalive-report.json` |
| Native Fluid-Physik | PASS | 12 numerische Prüfungen; `physics.json` |
| Offizielle Runtime-Integrität | PASS | 86 Dateien im ursprünglichen Vergleich unverändert; Lifecycle-Fingerprints aller installierten Kandidaten unverändert |
| Lumen / Velune | Nicht ausgeführt | Keine lokalen Runtimes vorhanden |

## Auslassungen der Default-Suite

13 Tests benötigen einen explizit konfigurierten externen Solver und wurden separat ausgeführt. Vier Tests sind POSIX-spezifisch; zwei benötigen historische Migrationsartefakte, ein Test eine fehlende alte CBOR-Paketfixture. Die drei Built-Artifact-Tests wurden ohne beauftragtes Packaging nicht ausgeführt. Diese Auslassungen sind keine erfolgreichen Prüfungen.

## Fehlversuche bleiben sichtbar

Der Erstbatch der konfigurierten Integrationstests ist in `production/configured-integration-first.log` archiviert. Die veraltete Status-Nachbedingung wurde durch die stärkere physische Prüfung von Alias, Ziel und Ownership nach dem Prozessende ersetzt. Zwei Launch-Fehler wurden durch Beschränkung der offiziellen Prozesssuche auf Nachfahren des eigenen Servers behoben; alle drei Stitch-Varianten bestanden danach mit laufendem Kollisionsprozess.

Der experimentelle Held-Pfad scheiterte unter Windows bei 250 Frames am offiziellen Hold-Dateiwriter. Der Produktionspfad verwendet den vollständigen nativen Dateitransport. Frühere Server-Abbruch-Testversuche scheiterten an inkonsistent rotierenden Checkpoints beziehungsweise an UTF-8-/Long-Path-Fehlern im Werkzeug. Der bestandene Versuch beendet den eigenen Prozessbaum erst nach einem echten verifizierten Checkpoint und prüft lange Pfade mit `io_path`.

Der 250-Frame-Fluidtest nutzt vier Cloth-Vertices und ein 100 × 100 × 51 Grid. Er ist kein High-Resolution-Cloth-Stresstest. VRAM war unter WDDM nicht pro Prozess messbar. Vollständige Details und Freigabegrenzen stehen im [Integrationsbericht](../../GAIA026_INTEGRATION_REPORT.md).

## Produktionsgrößen — 14. Oktober 2026, laufende Prüfung

Die ergänzende Produktionsmatrix ist im [Production-Scale-Bericht](../../GAIA026_PRODUCTION_SCALE_REPORT.md) dokumentiert. Dieser Bericht ist ein ausdrücklich gekennzeichneter Zwischenstand; laufende und ausstehende Fälle gelten nicht als bestanden.

Die vollständige Python-Suite der aktuellen Korrekturen wurde tatsächlich ausgeführt: **2.223 passed, 20 skipped, 3 deselected**. Das Log liegt unter `production-scale/unit-tests.txt`. Die unabhängige diskrete Oberflächenprüfung der vorhandenen Hauptmatrix-PC2-Dateien liegt unter `production-scale/independent-geometry.json`. Der installierte Artist-Workflow bleibt wegen fehlender nativer Einstellungen unvollständig geprüft. GAIA 0.23 bleibt ausgewählt; eine öffentliche Beta ist nicht freigegeben.

