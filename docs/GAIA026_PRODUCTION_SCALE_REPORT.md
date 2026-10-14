# GAIA 0.26 — Production-Scale Validation

Stand: 14. Oktober 2026, 14:54 Uhr. **Zwischenstand: Die native Testreihe läuft noch. Keine Beta-Freigabe.** Die Ergebnisse unten sind tatsächlich ausgeführte Prüfungen; ausstehende Wiederholungen werden nicht als bestanden gezählt.

Aktualisierung: NEON besteht nach der Streaming-Korrektur über **3, 60 und 250 Frames** mit den drei vorhandenen Cloth-Objekten (23.356 Vertices). Der native 250-Frame-Payload bleibt mit **979.260.464 Bytes** unter der unveränderten 1-GiB-Grenze. Alle drei PC2-Caches sind vollständig und endlich; Blender-Playback-Abweichung null. Der Re-Bake-/Fehlererhaltungstest und die 13 konfigurierten externen Solver-Tests sind ebenfalls bestanden. Die vollständige Suite nach den zusätzlichen Cache-Wiederverwendungsprüfungen: **2.232 passed, 20 skipped, 3 deselected**.

Für NEON/250 beträgt die Kontaktspitze 11.004, die maximale Weltgeschwindigkeit 0,384 m/s und die größte Kantenstreckung etwa 1,015. Die native Session misst 349,17 s für Simulation und Import; reine GPU-Kernelzeit ist nicht verfügbar. Die gesamte instrumentierte Blender-Prüfung benötigt 7.882,60 s, einschließlich Vorbereitung, mehrfacher Quell-/Cache-Prüfung und aufwendiger Nachauswertung der Szenenanimation. Blender-Peak-Working-Set: 5.934.546.944 Bytes. Szene und geprüfte FLIP-Dateien blieben unverändert. Die unabhängigen PC2-Prüfsummen-/Weltgeschwindigkeitstests liegen unter `production-scale/neon-250-independent-checks.json`.

Noch offene Grenzfälle bleiben offen: Der erneute 20.000-Vertex-/60-Frame-Lauf scheiterte bei Frame 18 an `contact_separated=false` trotz positiver CCD-/PCG-/Intersection-Free-Diagnostik. Die 50.000-/250-Messung wurde durch die Priorisierung von NEON pausiert und lief nach Wiederaufnahme in ihren Supervisor-Timeout. Sie belegt weder Solver-Instabilität noch einen bestandenen Langlauf; dafür ist ein frischer, durchgehender Versuch nötig. Die übrigen Kontroll- und Kombinationsfälle laufen weiter.

GAIA 0.23 bleibt ausgewählt. Keine Versionsnummer wurde für diese Prüfung geändert. Es wurden keine Commits, Tags, Pushes oder Veröffentlichungen vorgenommen.

## Prüfaufbau

Die Fixtures enthalten exakt 1.000 (25 × 40), 5.000 (50 × 100), 20.000 (100 × 200) beziehungsweise 50.000 (200 × 250) Cloth-Vertices. Die triangulierte Fläche ist 0,6 × 0,6 m groß. Eine Randreihe ist ausdrücklich mit Hard Pins fixiert. Self-Collision ist aktiviert. Ein zusätzlicher Fall enthält zwei Cloth-Objekte im selben Fluidfeld; deren Vertexzahl wird pro Objekt und insgesamt angegeben.

Die Tests rufen den vorhandenen Blender-Bake-Operator auf und pumpen dessen reguläre Timer-Callbacks im Hintergrundprozess. Sie benutzen echte GAIA-0.26-Ausgaben, native FluidVolume-Payloads und PC2-Playback. Host-seitige Ersatzkräfte werden nicht eingesetzt. Jede Stufe bereitet einen eigenen `.gaia`-Cache vor beziehungsweise verwendet ihren eigenen bereits vorbereiteten Cache.

Runtime: offizieller Windows/CUDA-Kandidat `official-2026-10-13-12-32-win64`, Commit `7232f4033515e1e72359f4ce5145456ce7d156f7`. Blender: 5.2.2 LTS. Hardware: Ryzen 9 7900X, RTX 4070 SUPER mit 12 GB, rund 63,1 GiB RAM. Die explizite Kandidatenauswahl gilt ausschließlich im jeweiligen Testprozess.

Fluidquelle der abgeschlossenen Fälle: `F:\16_Cloth_neXt\Flip_Test.blend`, 250 Frames bei 24 fps. Die neue Quelle `F:\16_Cloth_neXt\SW04\SW04_NEON.blend` hat 30 fps. Der nach Fertigstellung ausgeführte Readiness-Probe bestätigt 250 Particle-Dateien, 250 Velocity-Dateien und die Lesbarkeit des vollständigen Bereichs. Die Quelldatei blieb unverändert. Native NEON-Tests über 3, 60 und 250 Frames sind bestanden; die trockene NEON-Kontrolle ist noch ausstehend.

Auf Wunsch des Nutzers verwendet der NEON-Test die vorhandenen aktivierten Szenengeometrien: `S04_NEON_Garments` (14.946 Vertices), `S04_NEON_Garments.001` (4.379) und `S04_NEON_Garments.002` (4.031), insgesamt 23.356 Cloth-Vertices. Die vorhandenen Body-/Head-Collider werden übernommen, mit einer ausdrücklich beauftragten Ausnahme: `SW_04_body_lod0_mesh.001` wird in der Testkopie als Cloth-NeXt-Collider deaktiviert. Die beiden übrigen Collider bleiben aktiv. Vorhandene Materialien, Modifier, Pin-Einstellungen und Animationen bleiben erhalten; in dieser Szene ist Pinning derzeit deaktiviert. Es werden für NEON keine synthetischen Cloth-Flächen ergänzt. Die benötigten installierten Character-DNA-/Assembly-Add-ons werden im Testprozess geladen.

Während eines Teils der Messungen lief parallel der FLIP-Bake des Nutzers. Laufzeiten sind deshalb Messungen auf einer gemeinsam benutzten Workstation. Globale RAM-Auslastung und der Prozess-RSS des eigenen Solvers werden getrennt bewertet.

## Bisher abgeschlossene native Fälle

| Fall | Kurze Sequenz | 60 Frames | 250 Frames |
| --- | --- | --- | --- |
| 1.000 Vertices, native FLIP, Hard Pins | PASS, 3 | PASS | PASS |
| 5.000 Vertices, native FLIP, Hard Pins | PASS, 3 | PASS | PASS, zusätzlich wiederholt |
| 20.000 Vertices, ursprüngliche Qualitätswerte | CG-Iterationslimit bei Frame 2 | Nicht ausgeführt | Nicht ausgeführt |
| 50.000 Vertices, ursprüngliche Qualitätswerte | CG-Iterationslimit bei Frame 1 | Nicht ausgeführt | Nicht ausgeführt |
| 50.000 Vertices, kleinere Zeitschritte/höheres CG-Limit | PASS, 3 | PASS | Wiederholung ausstehend |
| 1.000 Vertices, Coupling Strength 0,05 | PASS, 12; Re-Bake abgeschlossen | PASS | PASS |
| 1.000 Vertices, Coupling Strength 1,0 | PASS, 12 | PASS | PASS |
| 1.000 Vertices, linear animierter Collider | PASS, 12 | PASS | PASS |
| Material Zones, Face Friction, periodischer Collider | PASS, 12 | PASS | PASS |
| Separater periodischer Collider | PASS, 12 | PASS | PASS |
| Zwei Cloth-Objekte, jeweils 1.000 Vertices | PASS, 12 | PASS | PASS nach Wiederholung |
| Gefaltete Cloth-Fläche | PASS nach Wiederholung, 12 | PASS, tatsächliche Selbstkontakte | PASS, tatsächliche Selbstkontakte |
| Trockene Kontrollläufe, 1.000 und 5.000 Vertices | Nicht vorgesehen | PASS nach Wiederholung | Nicht vorgesehen |
| NEON, drei vorhandene Cloth-Objekte, 23.356 Vertices | PASS, 3 | PASS | PASS |
| 20.000 Vertices, strengere Qualität (.0005 / 20000 / .0001) | PASS, 3 | PASS | PASS |

Die erfolgreichen 1.000-/5.000-Vertex-Langläufe enthalten ausschließlich endliche Koordinaten. Die maximale Geschwindigkeit beträgt ungefähr 4,89 m/s, die maximale Kantenstreckung etwa 1,03 und der Pin-Fehler etwa 10⁻⁷ m. Das aus Blender ausgelesene Playback stimmt mit den PC2-Samples überein. Beim periodischen Collider mit Material Zones bleiben die Geschwindigkeiten ebenfalls begrenzt; die vollständigen numerischen Messwerte stehen in den Fallartefakten.

Geometrieprüfungen erfassen Geschwindigkeit und Beschleunigung, Kantenstreckung, Flächenverhältnisse, Pins und Playback. Zusätzlich werden echte Dreiecksdurchdringungen an erstem, mittlerem und letztem Sample geprüft. Eine unabhängige Wiederholung dieser diskreten Oberflächenprüfung ist für die vorhandenen Hauptmatrix-PC2-Dateien bestanden. Diese Stichproben sind kein kontinuierlicher Kollisionsbeweis. Die frühere erste PC2-Datei eines Re-Bakes wurde ersetzt; ihre Skalartests sind erhalten, die unabhängige Wiederprüfung dieser Datei ist ausdrücklich nicht verfügbar.

Aktivierte Self-Collision allein beweist keinen tatsächlichen Selbstkontakt. Der gefaltete 60-Frame-Fall hat ohne zusätzliche Collider eine native Kontaktspitze von 109 und besteht die geometrischen Prüfungen. Der Zwei-Objekt-Langlauf hat 2.331 Kontakte als Spitze erfasst; die unabhängigen Cloth-Oberflächen durchdringen sich an Anfang/Mitte/Ende nicht. Bei Material Zones und Face Friction ist der kombinierte native Pfad geprüft. Eine isolierte Zuordnung der Bewegung allein zur Face Friction ergibt sich daraus nicht.

Die tatsächlichen trockenen 60-Frame-Kontrollen mit 1.000 beziehungsweise 5.000 Vertices bestehen. Gegenüber den passenden Fluidläufen beträgt die maximale Positionsdifferenz 0,0757 beziehungsweise 0,0728 m. Beide haben am Ende rund 0,0042 m RMS-Differenz. Dies ist ein beobachteter Wet-/Dry-Unterschied auf derselben Fixture, kein separat wiederholter Nachweis der Streuung zwischen Läufen.

Der gefaltete 250-Frame-Fall besteht: Kontaktspitze 99, maximale Geschwindigkeit 3,723 m/s, Kantenstreckung 1,044 und minimale Dreiecksfläche relativ zur Restfläche 0,962. Der neue 20.000-Vertex-/60-Frame-Versuch mit Zeitschritt 0,0005, CG-Limit 20.000 und Toleranz 0,0001 besteht in 1.093,26 s; sein 250-Frame-Test besteht in 3.038,69 s. Ein frischer 50.000-Vertex-Versuch stoppte vor dem Solve an Windows-Fehler 206 beim Anlegen eines langen Recovery-Verzeichnisses. Die Session verwendet dafür jetzt die bestehende Long-Path-I/O-Hilfe; 89 Session-Tests und die vollständige Suite (2.232 bestanden) sind grün. Der konkrete 50.000-/250-Wiederholungsnachweis nach dieser Pfadkorrektur steht noch aus.

## Grenzen, Fehler und Korrekturen

Die ersten 20.000-/50.000-Vertex-Fälle scheiterten am nativen CG-Limit von 2.500 Iterationen. Die Diagnostik meldete keine Kontakte und keinen Speicherfehler. Mit einem Zeitschritt von 0,001, 10.000 CG-Iterationen und Toleranz 0,001 bestanden 50.000 Vertices über 60 Frames. Der 250-Frame-Nachweis ist noch offen. Das ist eine beobachtete Konvergenzgrenze der ursprünglichen Einstellungen, keine belegte geometrische Obergrenze des Solvers.

Die RAM-Abbrüche erfolgten durch den bestehenden Schutz bei 90 % globaler Auslastung. Für diese frühen Abbrüche fehlt eine ausreichend genaue globale Momentaufnahme; der kleine RSS des eigenen Prozessbaums allein widerlegt keinen Systemdruck. Die parallel laufende FLIP-Simulation ist dokumentiert, aber nicht als alleinige Ursache bewiesen. Der Schutz bleibt aktiviert. Wiederholungen zeichnen die globale RAM-Auslastung zusätzlich auf.

Implementierte Korrekturen:

- Native Metriken und Prozess-Tails benutzen das tatsächliche Projekt-Ausgabeverzeichnis sowie Windows-taugliche lange Pfade.
- Kontaktspitzen aus nativen Metrikdateien bleiben erhalten, wenn das Controller-Log keine Kontaktsamples liefert.
- Die Blender-Diagnose für ein CG-Iterationslimit nennt passende Qualitätsparameter und erklärt den Fall ohne Kontakte.
- Exakte Float32-Darstellungen der RNA-Grenzwerte werden akzeptiert; Werte außerhalb des Bereichs bleiben ungültig. Dadurch wird der gültige minimale CG-Toleranzwert nicht schon vor dem Solve abgewiesen.
- Identische Fluidfelder mit disjunkten Cloth-Gruppen können denselben nativen FluidVolume-Payload verwenden. Unterschiedliche Einstellungen und überlappende Gruppen werden nicht zusammengelegt. Die strenge native Vergleichsmessung zweier 60-Frame-Läufe überschritt mit 0,005315 m Positionsdifferenz die Toleranz von 0,0001 m. Exakte native Gleichwertigkeit ist damit nicht nachgewiesen; Laufstreuung und Ursache sind noch ungeklärt.
- Der Ressourcen-Sampler ergänzt Prozessnamen und Kommandozeilen auch dann, wenn diese bei der ersten Beobachtung noch nicht verfügbar waren.

Ein früher Material-Zone-Versuch wurde durch den Entwicklungsmodus des Testwerkzeugs abgewiesen. Das Werkzeug verwendet jetzt die tatsächliche verwaltete Runtime und deren Capability-Prüfung. Der anschließend ausgeführte kombinierte Langlauf bestand. Das war eine Korrektur der Testauswahl, keine erfundene Solver-Capability.

## Payload und Ressourcen

### Korrektur für große Partikelmengen

Der unten dokumentierte ursprüngliche NEON-Speicherabbruch wurde durch einen direkten, begrenzten `.ffp3`-Leser korrigiert. Die Vorbereitung verarbeitet alle Partikel in Blöcken von 65.536, ohne vollständigen Blender-Partikelimport und ohne zusätzliche vollständige Float64-Framekopien. Die 512-MiB-Arbeitsgrenze bleibt bestehen. Der physische direkte Vergleich mit dem bisherigen Loader ist für Original-, Rotations- und Skalierungsfälle exakt gleich. Details stehen in [GAIA_LARGE_SIMULATIONS.md](GAIA_LARGE_SIMULATIONS.md).

Der neue native NEON-3-Frame-Test mit den vorhandenen 23.356 Cloth-Vertices besteht einschließlich aller drei PC2-Caches, Playback und diskreter Oberflächenprüfung. Maximale Weltgeschwindigkeiten: 0,393 / 0,056 / 0,132 m/s. Native Kontaktspitze: 8.382. Payload: 9.452.613 Bytes. Solve-/Fetch-/Import-Zeit: 8,22 s; Blender-Bake-Wallzeit einschließlich Vorbereitung/Auswertung: 98,63 s. Blender-Peak-Working-Set: 4.865.232.896 Bytes; dieser Wert enthält die komplette Szene und ist keine Messung des isolierten Rekonstruktionsspeichers. Die anschließenden 60- und 250-Frame-Läufe sind ebenfalls bestanden; ihre Messwerte stehen am Anfang dieses Berichts. Eine vorherige Auswertung hatte lokale PC2-Einheiten fälschlich als Meter behandelt; Geschwindigkeiten und Beschleunigungen berücksichtigen jetzt die tatsächlichen Objekttransformationen.

### Ursprünglicher Grenztest vor der Korrektur

NEON wurde mit den vorhandenen drei Cloth-Objekten und zwei aktiven Collidern tatsächlich über den regulären Bake-Operator gestartet. Die Szenenvorprüfung einschließlich Character-DNA-Treibern besteht. Beide 3-Frame-Anläufe scheitern jedoch vor dem nativen Solve an der vorhandenen 512-MiB-Arbeitsgrenze der Fluid-Rekonstruktion: Auflösung 128 ergibt 64 × 128 × 64 und geschätzte 683,2 MiB; Auflösung 100 ergibt 50 × 100 × 50 und 658,1 MiB. Frame 1 enthält 8.784.881 FLIP-Partikel. Die Schätzung enthält 72 Bytes pro Partikel, 96 Bytes pro Grid-Knoten und 32 MiB Reserve; allein der Partikelanteil plus Reserve überschreitet bereits 512 MiB. Daher reicht eine kleinere Grid-Auflösung für diese Quelle nicht aus. Das ist eine Integrationsgrenze vor dem Solver, kein nachgewiesener GAIA-Solver-Absturz. Diese Abbrüche betreffen den ursprünglichen Vorbereitungspfad. Mit der Streaming-Korrektur bestehen NEON/60 und NEON/250; der trockene Vergleich steht noch aus. Die `.blend`-Datei und die referenzierten FLIP-Cache-Dateien sind laut Nachprüfung unverändert. Weder diese Arbeitsgrenze noch die 1-GiB-Payload-Grenze wurde erhöht.

Die Grenze bleibt **1 GiB**. Ein tatsächlich ausgeführter Encoder-Budgettest überschritt diese Grenze nach 568 vollständig geschriebenen Feldsamples und brach sauber ab. Temporäre Payload-/Spool-Dateien wurden entfernt; der referenzierte `.gaia`-Cache und der vorherige gültige PC2-Cache blieben unverändert. Dieser Test prüft den Encoder-Abbruch, keinen abgeschlossenen Solve mit einem übergroßen Payload.

Die Fallartefakte erfassen Blender, Server, native Solver-Prozesse und Python-Worker getrennt. Probe-Prozesse dürfen nicht als eigentlicher Solver gewertet werden. Reine GPU-Kernelzeit ist nicht verfügbar; Solve-/Fetch-/Import-Zeit und Gesamtzeit werden entsprechend ihrem Messumfang bezeichnet. Prozessbezogener VRAM war unter WDDM nicht zuverlässig messbar und wird als nicht verfügbar angegeben.

Ein ursprünglicher 5.000-Vertex-/250-Frame-Lauf benötigte etwa 386,5 s; die Ressourcen-Wiederholung etwa 317,2 s. Diese Werte belegen einen abgeschlossenen Lauf, keinen isolierten Benchmark. Die endgültige Ressourcentabelle folgt nach Abschluss der laufenden Testreihe.

## Tatsächlich installierte Blender-Version und Artist-Workflow

Die tatsächlich installierte Extension meldet 2.9.22, der geprüfte Repository-Checkout 2.9.15. Es wurde keine Version geändert und keine Installation durch Testcode ersetzt.

Die installierte Extension lässt sich registrieren und die FLIP-Domain auswählen. Ihr fehlen jedoch die RNA-Einstellungen `water_flow_native_flow`, `water_flow_coupling_strength` und der Quick-Button `cloth_next.enable_flip_velocity_export`. Daher kann der angeforderte native Artist-Workflow mit dieser Installation nicht vollständig geprüft werden.

Die Vorbereitung im automatisierten Hintergrundprozess wartet auf den Companion-Bake-Window-Handshake. Der interaktive GUI-Schritt ist deshalb ungetestet; daraus wird kein bestätigter Fehler der interaktiven Vorbereitung abgeleitet. Native Aktivierung, Parameterwahl, Bake-/Fortschrittsanzeige, Playback und Re-Bake sind mit der tatsächlich installierten Extension in diesem Workflow **ungetestet**. Die regulären Operator-Tests des Checkouts ersetzen diesen Nachweis nicht.

## Regression und Freigabe

Die vollständige bestehende Python-Suite inklusive neuer Korrekturtests wurde ausgeführt: **2.232 passed, 20 skipped, 3 deselected**, 157,50 s. Die drei ausgelassenen Built-Artifact-Tests sind Packaging-Prüfungen. Die separat mit dem tatsächlichen GAIA-0.26-Kandidaten konfigurierten externen Solver-Tests sind bestanden: **13 passed**. Ein erster Aufruf verwendete einen Python ohne pytest und hat deshalb keine Tests ausgeführt; er wird nicht als Regressionsergebnis gezählt.

**Öffentliche Beta: derzeit NO-GO.** Die große Geometrie- und High-Res-Matrix ist noch nicht abgeschlossen; insbesondere fehlt der komplette Nachweis des nativen Artist-Workflows mit der tatsächlich installierten Extension. Erfolgreiche einzelne native Langläufe reichen dafür nicht aus.

Reproduzierbare Werkzeuge: `tools/blender_gaia026_production_scale.py`, `tools/run_gaia026_production_scale.py`, `tools/blender_gaia026_inspect_pc2.py` und `tools/report_gaia026_production_scale.py`. Arbeitsartefakte liegen unter `C:\Users\timch\AppData\Local\Temp\cnx026-production-scale-20261014`; archivierte Prüfbelege unter `docs/validation/gaia026/production-scale/`. Der Bericht wird nach Abschluss der noch laufenden Fälle aktualisiert.
