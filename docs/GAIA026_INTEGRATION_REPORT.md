# Cloth NeXt — GAIA 0.26 Production Stability & Long-Sequence Integration

Stand: 13. Oktober 2026. Der reguläre native Bake mit realen FLIP-Daten besteht die Stufen **3, 24, 60, 120 und 250 Frames** einschließlich Fetch, PC2 und Blender-Playback. Cancel/Resume, unerwarteter Server-Abbruch mit verifiziertem Checkpoint, Rebake sowie Save/Reload sind bestanden. Die abschließende 250-Frame-Wiederholung mit korrigiertem Prozessmodus besteht ebenfalls. **Technisches GO innerhalb des unten beschriebenen Prüfumfangs.**

## Umfang und unveränderte Quellen

Fluid-Dateiformat ist **`.gaia`**. `.cnx` bleibt das Authoring-Format, `.pc2` das Playback und `.blend` die Blender-Szene. Quelle ist `F:\16_Cloth_neXt\Flip_Test.blend` mit dem vollständig gebackenen FLIP-Particle-/Velocity-Cache. Alle 250 Quellframes wurden geprüft. SHA-256 der Szene: `3ddaf8270bcedebd7f797306bf368e7f547936b39dcca9e09144566521c6af2d`. Szene und 501 erfasste Cache-Dateistände blieben beim vollständigen Lauf unverändert.

Geprüft wurde der offizielle Windows-CUDA-Kandidat vom 13. Oktober 2026, Commit `7232f4033515e1e72359f4ce5145456ce7d156f7`, Protokoll 0.26, Schema 2. Offizielle Frontend-Dateien und Solver-Binaries wurden nicht gepatcht. Die Registry und Auswahl 0.23 bleiben unverändert. Manifestversion bleibt **2.9.15**; keine Commits, Tags, Veröffentlichung oder automatische Aktivierung.

## Ursachen und konkrete Korrekturen

### Windows-Pfade und Win183

Der ursprüngliche Fehler wurde auch ohne Solver beim Erstellen von Verzeichnissen durch Junctions unter `%LOCALAPPDATA%\ClothNeXt` reproduziert. Er trat in der Codex-Ausführungsumgebung auf. Derselbe Test aus der vom Benutzer geöffneten PowerShell und aus einem separat gestarteten normalen Windows-Prozess bestand vollständig. Der reguläre installierte 0.26-Bake besteht ebenfalls in diesem Prozesskontext. Damit ist kein genereller Defekt der offiziellen Windows-Runtime nachgewiesen. Der verantwortliche Windows-Filter wurde nicht bestimmt; `.cash` und ProjectLink wurden nicht umgebaut.

Die echten Solver-Prüfungen wurden anschließend in separaten normalen Windows-Prozessen ausgeführt. Blender ohne Long-Path-Unterstützung meldete in einem Testwerkzeug vorhandene Recovery-Dateien mit langen Pfaden fälschlich als fehlend. Das Werkzeug nutzt jetzt den vorhandenen `io_path`-Helfer; Recovery-Daten wurden dabei nicht verändert.

### PC2-Freigabe und Ownership

Beim Clear eines beibehaltenen Simulation Boundary werden Dateipfad, Playback-Owner und Render-/Viewport-Aktivierung entfernt. Abgetrennte Leser werden vor dem Löschen freigegeben. Nach Save/Reload werden Blender-relative `//`-Pfade vor dem Ownership-Vergleich aufgelöst. Dadurch besteht Bake → Rebake → Clear → Bake → Save → Reload → Clear. Ein fremder oder weiterhin gesperrter Leser führt weiterhin zur sicheren Erhaltung des vorherigen gültigen Caches.

### Native Fluid-Dateitransport

Der zunächst geprüfte Held-Pfad scheiterte bei 250 Frames an Windows-Dateiersetzungen. Die eigene Statusdatei erhielt eine prozessübergreifende Sperre und geschlossene private temporäre Dateien. Danach blieb ein Fehler im offiziellen `hold_at_frame`-Writer. Dieser wurde nicht gepatcht.

Der Produktionspfad erzeugt stattdessen die vollständige native Fluid-Zeitfolge als private CBOR-Datei und lädt sie mit dem bestehenden atomaren Dateitransport hoch. **Ein kontinuierlicher Solve**, offizielles `fields`/FluidVolume-Schema, keine neuen TCMD-Kommandos und keine nachträgliche Host-Korrektur der Cloth-Geschwindigkeit. Der Encoder verarbeitet jeweils einen Quellframe und schreibt Dateibereiche als gewöhnliche CBOR-Byte-Strings in höchstens 1-MiB-Stücken. Die `.gaia`-Quelle bleibt unverändert.

Grenzen: 32 MiB je gepacktem Frame, 128 MiB für den aktiven Array-Preflight und 1 GiB für die fertige PARAM-Datei. Diese Werte sind keine Obergrenzen für den gesamten Blender- oder Solver-Prozess. Der offizielle Empfänger hält die vollständige Zeitfolge; sein Speicherbedarf wächst mit der Sequenz. Form, Checksummen, Units und Velocity-Policy werden geprüft. Cancel entfernt eigene unvollständige Dateien und Spools; der Upload prüft Cancel zwischen begrenzten Chunks.

### Falsche Adoption fremder Prozesse

Zwei Stitch-Läufe scheiterten sporadisch mit „Adopting externally-launched solver“. Der offizielle Monitor sucht standardmäßig auch außerhalb seines eigenen Prozessbaums nach passend benannten Prozessen. Für den von Cloth NeXt gestarteten modernen Server wird jetzt die bereits offiziell unterstützte Umgebungseinstellung `PPF_SOLVER_SCAN_DESCENDANTS=1` gesetzt.

Alle drei nativen Stitch-Varianten bestanden anschließend bei gleichzeitig laufendem separatem Prozess mit absichtlich kollidierendem Namen. Der fremde Prozess blieb während der Prüfungen aktiv und wurde danach als eigener Testprozess beendet. Externe Server werden durch diese Einstellung nicht verändert.

## Echte lange Sequenzen

Hardware: RTX 4070 SUPER mit 12 GB, Ryzen 9 7900X, 63,1 GB RAM; Blender 5.2.2 LTS, FLIP Fluids 1.8.8. Der Fluid-Gate verwendet ein 100 × 100 × 51 Grid und ein frei bewegliches Cloth mit vier Vertices. Für jede Stufe wurde ein identischer trockener Kontrolllauf durchgeführt.

| Frames | Fluid-Laufzeit s | PARAM-Datei Bytes | Größter gepackter Frame Bytes | Blender OS-Spitzenspeicher Bytes |
|---:|---:|---:|---:|---:|
| 3 | 13,81 | 1.719.959 | 643.708 | 455.348.224 |
| 24 | 38,37 | 28.971.009 | 1.995.388 | 679.428.096 |
| 60 | 89,19 | 106.021.061 | 2.265.724 | 799.961.088 |
| 120 | 168,03 | 226.434.107 | 2.282.108 | 801.214.464 |
| 250 | 365,88 | 474.231.289 | 2.282.108 | 822.972.416 |

Alle PC2-Samples sind endlich; alle Blender-Playback-Frames stimmen mit PC2 überein, maximaler gemessener Fehler **0,0**. Der 250-Frame-Dry-Lauf dauerte 158,77 s. Die Fluid-Datei wurde in 108,32 s encodiert. Am letzten Frame beträgt die Fluid-/Dry-Abweichung 31,895 m bei diesem frei beweglichen Minimalfixture. Das beweist wirksame Kopplung und vollständigen Transport, keine allgemeine Aussage über physikalische Qualität beliebiger Produktionsszenen.

Bei 250 Frames wurden 1.673.543.680 Bytes für die abgetastete Prozessbaum-RSS beobachtet; Server OS-Peak 971.296.768 Bytes, nativer Solver 156.495.872 Bytes. Die erste Messung verlor den individuellen Python-Build-Eintrag bei einer fehlgeschlagenen Prozessmetadatenabfrage; der Sampler wurde für die Wiederholung korrigiert. Prozessbaum-RSS und individuelle OS-Peaks sind unterschiedliche Messgrößen. WDDM lieferte keine nutzbare Prozess-VRAM-Messung; die 12 GB Kartenkapazität wird nicht als gemessener Verbrauch ausgegeben.

Die abschließende Wiederholung mit unverändert wiederverwendeter `.gaia`-Datei bestand in **237,33 s** (Encoding 73,30 s); der bereits geprüfte identische Dry-Kontrolllauf wurde ausdrücklich wiederverwendet. Die PARAM-Datei bleibt 474.231.289 Bytes. Alle 250 Playback-Frames sind korrekt, temporäre Payloads vollständig entfernt. Der korrigierte Sampler erfasste Blender 700.903.424 Bytes, Python-Build 1.018.802.176 Bytes, Server 971.198.464 Bytes und nativen Solver maximal 175.923.200 Bytes OS-Peak; abgetastete Prozessbaum-RSS 1.658.372.096 Bytes. Der Python-Build-Peak zeigt ausdrücklich, dass die gesamte offizielle Frontend-Verarbeitung nicht unter die Array-Preflight-Grenze fällt. Nachweis: [Wiederholung](validation/gaia026/production/250-confirm-report.json) und [Ressourcen](validation/gaia026/production/250-confirm-resources.json).

## Stability und Regression

- Regulärer Session → Upload → Build → Solve → Fetch → PC2 → Playback bestanden.
- Rebake, Clear, Dateifreigabe, Save/Reload sowie Dry- und Fluid-Cancel/Resume in einem neuen Blender-Prozess bestanden.
- Absichtlicher Abbruch des eigenen Server-Prozessbaums nach verifiziertem SavedState: erwartete Fehlerdiagnose, sechs erhaltene PC2-Frames, Checkpoints 1/3/5; Resume im neuen Prozess beendet alle 24 Frames.
- Echte Runtime-Wechsel 0.26 → 0.23 → 0.22 → 0.26 bestanden; Registry und installierte Dateien unverändert.
- Statische, rigid animierte und deformierend animierte Collider-Attachments sowie drei Stitch-Varianten bestanden.
- `.gaia`-Wiederverwendung unverändert; korrumpierte eigene Cache-Kopie wird mit Checksum-Fehler abgelehnt, vorheriges gutes PC2 bleibt erhalten.
- Tatsächlicher Idle-Timer mit Testwert 3 s: ohne Requests Ende nach 3,016 s; 21 Pings über 8,266 s halten ihn aktiv, danach Ende nach 3,015 s. Regulärer 8-Frame-Solve mit laufendem Polling besteht ebenfalls. Produktionsdefault 3600 s bleibt unverändert.

Vollständige Suite: **2211 passed, 20 skipped, 3 deselected**, 144,85 s. Die 13 standardmäßig unkonfigurierten externen Integrationstests wurden separat gegen 0.26 ausgeführt. Der erste Batch enthielt 10 Pass und drei Fehler: eine veraltete `NO_DATA`-Nachbedingung sowie zwei Prozess-Adoptionsfehler. Nach Korrektur bestanden die physische Cleanup-Nachbedingung und alle Stitch-Varianten. Fehlgeschlagene Erstläufe bleiben als Evidenz erhalten; Ergebnisse werden nicht als ein einziger fehlerfreier Batch dargestellt.

## Grenzen und Freigabe

Die 250-Frame-Prüfung zertifiziert vollständigen Fluid-Transport und Playback am Minimalcloth. Sie ersetzt keinen Langzeit-Stresstest mit hochaufgelöstem Produktionscloth oder mehreren großen Fluid-Targets. Kleinere Geometrie-/Attachment-/Stitch-Regressionen wurden separat ausgeführt. Resume benötigt einen kompatiblen verifizierten SavedState und einen passenden PC2-Prefix; ein zu früher oder inkonsistenter Abbruch wird sicher abgelehnt. Ein allein beendeter Kontrollserver kann den weiterlaufenden nativen Prozess Checkpoints rotieren lassen; dieser negative Test wird nicht als erfolgreicher Resume ausgegeben.

Lumen und Velune sind lokal nicht installiert und wurden nicht zertifiziert. Surface-only/VDB, andere Unit-Scale-Konfigurationen und bidirektionale FLIP-Kopplung sind nicht Bestandteil dieser Freigabe. Der geprüfte Pfad verwendet unveränderte unclamped Liquid-Particle-Velocity, Unit Scale 1 und Baked World Scale 1.

**GO für den geprüften nativen 0.26-Fluidpfad und die geprüften Regressionen.** Kein Nachweis für beliebig große Produktionsszenen. Veröffentlichung und automatische 0.26-Aktivierung bleiben aus; ausgewählt bleibt 0.23.

Evidenz: [Testresultate](validation/gaia026/TEST_RESULTS.md), [Produktionsnachweise](validation/gaia026/production/long-sequence-report.json), [Runtime-Integrität](validation/gaia026/runtime-comparison.json). Offizielle Prozesslogik: [Monitor](https://github.com/st-tech/ppf-contact-solver/blob/7232f4033515e1e72359f4ce5145456ce7d156f7/crates/ppf-cts-server/src/monitor.rs), [Prozesssuche](https://github.com/st-tech/ppf-contact-solver/blob/7232f4033515e1e72359f4ce5145456ce7d156f7/crates/ppf-cts-core/src/utils.rs).

