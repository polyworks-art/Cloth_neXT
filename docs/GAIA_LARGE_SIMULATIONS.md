# Große FLIP-Simulationen vorbereiten

Der FLIP-Liquid-Particle-Pfad verarbeitet Positionen und Geschwindigkeiten direkt aus den `.ffp3`-Dateien in Blöcken von höchstens 65.536 Partikeln. Alle Surface-, Boundary- und Interior-Partikel gehen in die Rekonstruktion ein. Die Vorbereitung importiert dafür keinen vollständigen Partikel-Mesh in Blender und legt keine vollständige zusätzliche Float64-Kopie eines Frames an.

Die vollständigen Quelldateien werden weiterhin auf Integrität und Änderungen geprüft. Bounds-Dateien gehören jetzt ebenfalls zum Fingerprint. Die Transformation entspricht dem FLIP-Loader; die exportierten physischen Geschwindigkeiten werden weder gedreht noch durch eine Display-Skalierung verändert. Die Rezeptkennung `flip-streamed-world-v3` sorgt dafür, dass abgeleitete Caches mit anderer Vorbereitung nicht ungeprüft weiterverwendet werden. Das Ausgabeformat bleibt `.gaia`.

Der Speicherbedarf der Rekonstruktion hängt damit von der Grid-Größe und einem festen Partikelblock ab, nicht von der gesamten Partikelzahl eines Frames. Das bestehende 512-MiB-Arbeitsbudget und die Per-Frame-Grenzen bleiben bestehen. Für das NEON-Grid 64 × 128 × 64 beträgt die konservative Arbeitsschätzung inklusive Partikelblock und Reserve etwa 84,5 MiB. Das ist eine Schätzung für die Rekonstruktion, kein Limit für den gesamten Blender-Prozess: Szene, Rig, sichtbare Caches und Solver benötigen zusätzlichen Speicher.

Die native Sequenz besitzt weiterhin eine 1-GiB-Payload-Grenze. Partikelstreaming hebt diese Grenze nicht auf. Besonders große räumliche Grids oder sehr lange native Sequenzen können deshalb weiterhin an einer klaren Grenze stoppen. Ein vollständiger Nachweis beliebig großer Grids oder Sequenzen ist damit nicht erbracht.

Dateifehler, unterschiedliche Position-/Velocity-Layouts, NaN/Inf und Abbruch werden geprüft. Dateien werden beim Abbruch geschlossen; der atomare `.gaia`-Writer erhält den vorherigen gültigen Cache. Der Surface-Velocity-Pfad und vorhandene Array-Provider bleiben kompatibel.

Nachweise vom 14. Oktober 2026:

- Direkter Vergleich mit dem installierten FLIP-Loader: Positionen, Geschwindigkeiten, Grid-Velocity und Influence exakt gleich in der geprüften Quelle; zusätzlich mit Rotation/Translation sowie nicht gleichmäßiger Skalierung geprüft.
- Unit-Test mit 8.388.608 Partikeln ohne vollständig residenten Eingabeframe; Prüfungen von Chunk-Grenzen, defekten Dateien, spätem NaN/Inf, Abbruch und Erhaltung eines vorhandenen gültigen `.gaia`-Caches.
- NEON: nativer 3-Frame-Bake mit 8.784.881 Partikeln im ersten Frame, 128er Feld, 23.356 Cloth-Vertices und zwei aktiven Collidern bestanden. Alle drei PC2-Caches sind endlich; Playback-Abweichung null. Die `.blend`-Quelle und die geprüften FLIP-Dateien blieben unverändert.
- NEON über 60 und 250 Frames bestanden, einschließlich Blender-Playback und diskreter Oberflächenprüfung. Der 250-Frame-Payload beträgt 979.260.464 Bytes und bleibt unter 1 GiB. Die maximale geprüfte Weltgeschwindigkeit beträgt 0,384 m/s.

Mehrere Cloth-Objekte können innerhalb desselben Bake-Jobs einen bereits vollständig geprüften `.gaia`-Cache wiederverwenden. Dazu müssen Quelle, Feldrezept, Grids und Zielpfad übereinstimmen; Quell- und Cache-Dateistempel werden erneut kontrolliert. Ein neuer Bake führt wieder die vollständige Quellprüfung aus. Unterschiedliche Fluid-Quellen oder Grids dürfen nicht dieselbe `.gaia`-Datei überschreiben: Die Vorbereitung meldet dann den Konflikt und erhält den ersten gültigen Cache.

Die Änderung liegt im Repository-Checkout. Die installierte Extension, Versionsnummern und die Auswahl von GAIA 0.23 bleiben unverändert. Den aktuellen Freigabestand dokumentiert [der Produktionsbericht](GAIA026_PRODUCTION_SCALE_REPORT.md).
