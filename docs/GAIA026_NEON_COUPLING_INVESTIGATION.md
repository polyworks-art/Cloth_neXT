# NEON: native Fluidkopplung und Materialvergleich

Stand: 15.10.2026. Lokale Untersuchung, keine Veröffentlichung, Versionsänderung des Add-ons, Commits oder Pushes. Originalszene und FLIP-Quelldaten unverändert. Keine neue 250-Frame-Simulation.

**Ergebnis:** Die geringe Bewegung hat mehrere belegte Ursachen: kaum Fluidkontakt am zweiten Objekt sowie eine stark begrenzte Reaktion durch das Materialpaket und Kontakte am Hauptteil. Ein leichteres, weicheres Material zeigt dort reproduzierbar mehr native Fluidwirkung. Eine deutlich sichtbare Fluidreaktion der gesamten ursprünglichen NEON-Kleidung ist weiterhin **nicht nachgewiesen**. Weder Material-Damping noch ein nachgewiesener Fehler bei Velocity-Skalierung oder Koordinaten erklären den untersuchten Fall.

## Tatsächliche Konfiguration

Gemeinsame Werte: Fabric/Baraff-Witkin, Material-Damping Shape/Fold **0/0 s**, Pinning aus, Kontaktabstand 1 mm, Offset 0. Fluid Influence, Coupling Strength, Velocity Scale und Flow jeweils **1**. Die Stretch-Werte sind direkte, bereits dichtenormierte Solverwerte, keine gewöhnlichen Moduli in Pascal. Shear hat keinen separaten Exportparameter; das Modell erhält `young-mod` und `poiss-rat`.

| Cloth-Objekt | Preset | kg/m² | young-mod | poiss-rat | bend | Stretch-Limit | Solver-Reibung |
|---|---|---:|---:|---:|---:|---:|---:|
| S04_NEON_Garments | MIT_OUTDOOR_POLYESTER | 0,207297 | 8000 | 0,32 | 3,807 | 4 % | 0,175 |
| .001 | CUSTOM | 0,465 | 50000 | 0,14 | 20 | 4 % | 0,24 |
| .002 | PRODUCT_YAMAMOTO_39_3MM | 0,56 | 420 | 0,48 | 2,8 | 32 % | 0,275 |

Die UI-Reibungswerte 0,35/0,48/0,55 werden gemäß bestehendem Mapping halbiert. `.001` hat die Zone **Inlay**: 594 von 8817 Dreiecken erhalten young-mod 10, bend 2 und Solver-Reibung 0,05; die übrigen 8223 erhalten die Grundwerte. Das sind harte Flächenzuordnungen, keine interpolierten Vertex-Gewichte. Die Zone macht diesen Bereich weicher. Keine gewichteten Pin-Vertices. Weitere Attachments: 102 Punkte von `.002` zum Körper sowie 84 vom Hauptteil zu `.002`, jeweils Stitch Strength 1. Keine Inflations-, Schrumpf- oder aktiven Plastizitätseffekte.

**Velocity Damping 1 ist wirksam, aber kein Bewegungsstopp.** Der unveränderte offizielle Referenzcode verwendet bei diesen Einstellungen den Fluidterm

`a = Field Weight × Coupling × Occupancy × (U − Flow × v)`.

Flow 1 gleicht die Vertexgeschwindigkeit an die lokale Fluidgeschwindigkeit an; stilles Wasser bremst bewegten Stoff. Flow 0 entfernt diesen geschwindigkeitsabhängigen Anteil. Das ist unabhängig vom hier ausgeschalteten Material-Damping. Der native freie Referenzfall misst bei U = 2 m/s, Strength 2 eine Endgeschwindigkeit von **1,71737 m/s**, erwartet **1,71773 m/s**. Alle zwölf Referenzprüfungen bestanden.

## Fluid direkt an der Kleidung

Tatsächlich gespeichert ist für alle drei Objekte `S04_NEON_Garments_002_WaterFlow.gaia`: Grid 64×128×64, 30 FPS, unclamped m/s, Baked World Scale 1. Die anders benannte hochauflösende Datei war eine erste Vergleichsrekonstruktion und wird nicht als gebundene NEON-Datei gewertet.

| Objekt | Frames mit Occupancy > 0 / 250 | Anteil belegter Vertices, Bereich | Max. Fluidgeschwindigkeit an Cloth |
|---|---:|---:|---:|
| Hauptteil | 250 | 50,8–95,0 % | 3,426 m/s |
| .001 | 2, nur Frames 249–250 | 0–17,9 % | 1,468 m/s |
| .002 | 245 | 0–77,5 % | 2,781 m/s |

Diese Vollbereichswerte sind **hostseitige Messungen an den historischen PC2-Positionen**, mit dem nativen Packing und dem offiziellen räumlichen Sampler. Sie sind keine Messung historischer Solverkräfte. Der finale Upload-Payload des alten Bakes ist nicht erhalten.

Zusätzlich wurden die **wirklich geschriebenen nativen Solver-Input-Bricks**, Transformdateien und Target-Masks der neuen 30-Frame-Läufe ausgelesen. Alle drei Cloth-Masks sind 1. Sampling einschließlich Frame 15,5 nutzt die unveränderte offizielle räumliche und zeitliche Interpolation. An Frame 30:

| Objekt | belegte Vertices | Fluidgeschwindigkeit Mittel / Max | Vertexgeschwindigkeit Mittel | relative Geschwindigkeit Mittel |
|---|---:|---:|---:|---:|
| Hauptteil | 77,15 % | 0,29450 / 0,73711 m/s | 0,000498 m/s | 0,29442 m/s |
| .001 | 0 % | 0 / 0 | 0,000151 m/s | 0,000151 m/s |
| .002 | 26,22 % | 0,06865 / 0,67125 m/s | 0,000238 m/s | 0,06882 m/s |

Die Quellprüfung rekonstruiert Frames 1/30/125/250 erneut aus jeweils etwa **8,78 Millionen echten FLIP-Partikeln**. Die Quell-Hashes stimmen; Velocity- und Occupancy-Abweichung zum gebundenen Cache jeweils **0**. Native Volume-Step ist 1/30 s, Start 0; World Scale, Velocity Scale und Zeitraten ergeben keinen zusätzlichen Skalierungsfaktor. Lokale PC2-Millimeter werden mit dem exportierten 0,001-Scale und der X-Rotation in Weltkoordinaten umgerechnet; die initialen nativen Positionen wurden gegen diese Transformation geprüft. Keine reine Local-Space-Abweichung wird als Meter ausgegeben.

Der maximale initiale Transformationsfehler gegen die nativen Positionen beträgt **5,96×10⁻⁸ m**. Vertexweise Geschwindigkeiten sind zeitliche Differenzen der Positionen, keine direkt ausgelesenen internen Newton-Geschwindigkeiten. Der historische PC2-Diagnosepfad verwendet am ersten Sample die Vorwärtsdifferenz. Daraus abgeleitete Kräfte bleiben ausdrücklich Host-Erwartungen.

## Kontrollierte Simulationen

Jeweils Frames 1–30, 30 FPS, identische exportierte Geometrie, Startzustände, Attachments, Collider, Schwerkraft und Qualität (`dt=0,01`, CG 2500, Tol 0,01). Originalmaterial-Kontrollen berücksichtigen die gespeicherte Inlay-Zone. Zwei Dry- und zwei Wet-Läufe pro Material. Verwendet wurden getrennte Kopien der Solverexports über die unveränderte offizielle Frontend-API; die Original-.blend wurde nicht bearbeitet. Der erfasste Körper ist in diesen 30 Frames stationär; World- und körperrelative Differenzen sind daher identisch.

Das leichte Testmaterial verwendet **0,08 kg/m², young-mod 200, poiss-rat 0,35, bend 0,1, Shape/Fold Damping je 0,0005 s**. Kontaktparameter und Stretch-Limits bleiben erhalten. Keine Steifigkeit wurde auf null gesetzt. Dieses Material ist eine Diagnosevariante, keine neue globale Voreinstellung und kein kalibriertes Ersatzmaterial für Neopren.

Alle folgenden Werte sind RMS über sämtliche Vertices und Frames, in **mm**:

| Objekt | Original Wet/Dry, zwei Läufe | Original Dry-Streuung | Leicht Wet/Dry, zwei Läufe | größte leichte Dry-/Wet-Wiederholungsstreuung |
|---|---:|---:|---:|---:|
| Hauptteil | 0,126–0,140 | 0,083 | **0,897–0,921** | 0,251 |
| .001 | 0,028–0,048 | 0,039 | 0,183–0,216 | 0,210 |
| .002 | 0,021–0,032 | 0,036 | 0,051–0,068 | 0,057 |

Am leichten Hauptteil beträgt die maximale Wet-/Dry-Abweichung 7,72–7,78 mm. Der Effekt ist reproduzierbar größer als die gemessene Laufstreuung. Bei `.001` fehlt während dieser Framerange direkter Fluidkontakt; Unterschiede können durch das gekoppelte Cloth-/Kontakt-System entstehen. Bei `.002` ist kein klar von der Streuung getrennter Effekt belegt.

Ein zusätzlicher Vergleich mit **sämtlichen Kontakten deaktiviert**, Attachments erhalten, ergibt am Hauptteil 3,63 mm RMS und 14,80 mm maximal. Er isoliert den Gesamteinfluss von Körper- **und** Selbstkontakten, nicht ausschließlich Body-Collider oder Reibung. Ohne Kontakte fällt `.001` in andere Fluidregionen; seine große Differenz darf nicht dem ursprünglichen Kontaktzustand zugeschrieben werden. Der Strength-5-Vergleich ist nur explorativ; Strength wurde nicht global erhöht.

Native Kontaktspitzen Original Wet → leicht Wet: **2259→5716**, **1563→3721**, **8564→9598**. Das sind Kontaktzahlen, keine Anteile eindeutig fixierter Vertices. Alle Kontrollen blieben endlich; Dreiecksflächen blieben positiv. Maximale Kantenlängenverhältnisse Original → leicht: **1,024→1,037**, **1,008→1,015**, **1,122→1,203**. Mittlere absolute Biegeänderung: **1,13→3,88°**, **0,34→2,58°**, **2,48→3,34°**. Das leichte Material faltet und komprimiert lokal stärker; es ist nicht pauschal als Produktionspreset freigegeben. Die Wirkung einzelner Materialparameter wurde nicht unabhängig voneinander isoliert.

![Materialvergleich](validation/gaia026/coupling-investigation/material-comparison.png)

## Korrekturen und Fortschritt

Nachgewiesener separater Cachefehler: Beim Speichern vorbereiteter Scenes gingen die ausgelagerten Material-/Reibungs-/Stitch-Bindings verloren. Sie werden jetzt als hash-geprüftes `bind.json` mitgespeichert und auf warmem Cache wiederhergestellt. Intra-Seams werden nicht doppelt angelegt; geänderte aktuelle Parameter bleiben Teil der Recovery-Identität. Material-Zonen-Grundwerte beeinflussen die Cache-Identität; alte unvollständige Pläne werden verworfen. Die interne Cacheformat-Kennung wurde angepasst, keine Add-on- oder Solver-Releaseversion. Der untersuchte historische Bake war ein Scene-Cache-Miss; dieser Fehler ist **nicht als Erklärung seiner gesamten schwachen Bewegung nachgewiesen**.

Fortschrittskorrekturen: getrennte Kennungen für Scene Encoding, Native Fluid Encoding, Upload und Solver Build; Blender akzeptiert jetzt die bisher ignorierten nativen `EXPORTING`-Events. Mehrere unterschiedliche Fluidtargets zählen fortlaufend statt nach dem ersten Target scheinbar fertig zu sein. Vorhandene Capture-, Label-, ETA- und Importkorrekturen bleiben erhalten.

Ein echter kurzer Operatorlauf bestätigt Capture, Scene Encoding, Native Fluid Encoding **0→1→2→3 / 3**, Upload und Build als getrennte Aktivitäten. Native Encoding wird beim Phasenwechsel nicht als Gesamtabschluss ausgegeben. Alle ETA-Werte vor der Simulation bleiben `None`. Der Operator scheitert am bekannten **WinError 183 beim Projekt-Alias/.cash** vor der Simulation. Deshalb sind die vollständige Companion-Anzeige, Solver-ETA, Fetch, PC2-Import und Cleanup nach diesen Änderungen **nicht vollständig durch einen erfolgreichen Operator-Bake validiert**. Die direkt über die offizielle API ausgeführten numerischen Kontrollen werden davon getrennt bewertet.

Profiling statt Vermutung: Historisch 336,530 s für 250 Frameauswertungen/500 Collider-Samples. Im neuen echten 3-Frame-Capture: **1,358 s gesamt**, davon Frame Set **0,776 s**, Mesh Capture **0,256 s**, Depsgraph-Zugriff **0,000037 s**. Für die historische Zahl fehlt die Unterteilung; dieser kurze Ausschnitt rechtfertigt weder eine verlässliche Hochrechnung noch eine Änderung der Animationsauswertung. Beide Capturepfade erhalten nun getrennte Messzeiten. Keine spekulative Evaluations-Abkürzung eingeführt. Profiler-Threadzeiten überlappen und werden nicht zur Bakezeit addiert.

[Echte Operator-Aktivitäten und Capture-Messzeiten](validation/gaia026/coupling-investigation/operator-progress-profile.json), [Profiler-Auszug](validation/gaia026/coupling-investigation/capture-profile.txt).

## Bewertung, Tests und Artefakte

- **Belegte Ursachen:** `.001` liegt nahezu die ganze Sequenz außerhalb belegten Fluids; beim Hauptteil begrenzen Materialpaket und Kontakte die Fluidreaktion deutlich. `.002` hat im kurzen Test wesentlich weniger lokale Strömung und zahlreiche Kontakte; die genaue Aufteilung seiner Ursachen bleibt offen.
- **Kein belegter Fehler** bei nativer Feldzuordnung, Quellrekonstruktion, World-/Local-Umrechnung, FPS oder Velocity-Skalierung in den neuen Kontrollen. Native Fluidwirkung am freien Referenzpatch und am leichten NEON-Hauptteil numerisch belegt. Eine große sichtbare Reaktion der ursprünglichen gesamten NEON-Kleidung bleibt offen.
- **Empfehlung:** Influence/Velocity Scale/Coupling/Flow zunächst bei 1 belassen. Für das Hauptteil die dokumentierte leichte Variante nur in einer Testkopie verwenden und anschließend Stiffness/Bending einzeln abstimmen. Bei `.001` zuerst nachweisbaren Fluidkontakt herstellen, nicht das Material für einen trockenen Bereich verändern. `.002` nicht ungeprüft auf ein leichtes Gewebe umstellen. Keine globalen Preset-Änderungen.
- **Tests:** letzte gezielte Suite **351 passed**. Gesamtsuite vor der abschließenden Recovery-Identitäts-Ergänzung: **2297 passed, 20 skipped, 3 deselected, 1 failed**. Der Fehler ist `test_extension_source_root`: vorhandene generierte Companion-Dateien im Sourcebaum (Zeitstempel 14.10., 15:03/15:20, vor der Untersuchung). Diese wurden erhalten. Die abschließende gezielte Suite umfasst Bridge-, Cache-, Session-, Bake-, Companion-, Native-Fluid- und UI-Regressionen. Zwölf echte native Referenzfälle bestanden. Fehlgeschlagene Alias-/Erstversuche bleiben dokumentiert.
- **Reviewbare Resultate:** [numerische Auswertung](validation/gaia026/coupling-investigation/numerical-summary.json), [alle 250 Host-Kontaktmessungen](validation/gaia026/coupling-investigation/all-frame-contact.json), [Quellvergleich](validation/gaia026/coupling-investigation/source-reconstruction.json), [native Referenz](validation/gaia026/coupling-investigation/free-patch-reference.json), [gezielte Tests](validation/gaia026/coupling-investigation/final-focused-tests.txt), [Gesamtsuite](validation/gaia026/coupling-investigation/full-tests.txt).

Zwölf neue 30-Frame-Test-PC2s für Original/leicht × Wet/Dry sind unter [preview-pc2](validation/gaia026/coupling-investigation/preview-pc2/manifest.json) abgelegt und gegen die nativen Weltpositionen rückgeprüft. Das bestätigt die Serialisierung, ersetzt aber keinen erfolgreichen Blender-Operator-Import. Die Geometrieabbildung überzeichnet keine Verschiebungen:

![Leichtes Material, Frame 30](validation/gaia026/coupling-investigation/soft-wet-dry-geometry.png)
