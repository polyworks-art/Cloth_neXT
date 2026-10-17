# GAIA – experimentelle native FLIP-Hydrodynamik auf CPU und CUDA

Nachfolgender Integrationsstand: [erster regulärer Blender-End-to-End-Test](GAIA_HYDRODYNAMIC_BLENDER_E2E.md).
Test A 131–133 inklusive PC2/Reload/GUI-Cancel bestanden; der 30-Frame-Test
ist wegen offener/nicht-manifold Quelloberflächen noch blockiert. Normale
Installationen bleiben gesperrt; nur ein isolierter, identitätsgeprüfter Testpfad ist aktiv.

Stand: 16.10.2026. Experimenteller Build, 20 vollständige GPU-Referenzszenarien,
direkter GPU-Kraft-/Matrix-Test und fünf vollständige NEON-GPU-Läufe bestanden.
Der strenge CPU-/CUDA-Kraftvergleich besteht in 19 von 20 Fällen. Auch alle fünf
CPU-NEON-Fälle sind inzwischen aus Checkpoints fortgesetzt und vollständig
abgeschlossen; der große Backend-Vergleich ist ausgewertet. Keine Produktfreigabe.

Der Build erfolgt ausschließlich im separaten Checkout
`C:/Users/timch/Documents/GitHub/ppf-contact-solver-hydrodynamics`.
Offizielle Solver-Binaries, Treiber und die Systeminstallation bleiben unverändert.
Die CPU-Referenzläufe verwenden weiterhin das eingefrorene `ppf-contact-solver-spatial-r3.exe`.

## Build-Ursache und Vorgehen

`LNK1181: libsimbackend_cuda.lib` entsteht, wenn Cargo vor dem Windows-Backend-Bootstrap
gestartet wird. Die Build-Abhängigkeit `ppf-cts-compute` benötigt diese Importbibliothek
bereits zum Linken des Solver-Buildscripts. Der vorhandene offizielle Buildablauf baut
deshalb zuerst die Backend-DLL mit `build-win-native/scripts/build-cuda.bat`.
Der experimentelle Build folgt dieser Reihenfolge.

Die vorhandene Systemtoolchain ist CUDA 13.2. Der Solver verlangt CUDA 12.8; sein
Backend verwendet unter anderem ein in CUDA 13 entferntes `cudaDeviceProp`-Feld.
Die Versionsprüfung wird nicht umgangen. Stattdessen werden `cuda_nvcc`, `cuda_cudart`
und `cuda_cccl` aus dem [offiziellen NVIDIA-Manifest für CUDA 12.8.1](https://developer.download.nvidia.com/compute/cuda/redist/redistrib_12.8.1.json)
in `.tools/cuda-12.8` entpackt und mit den manifestierten SHA256-Prüfsummen geprüft.
Es wird kein Installer ausgeführt.

Der Build verwendet MSVC aus `C:/BuildTools`, eine eigene Cargo-Zielstruktur
`target/hydrodynamics-cuda` und dieselben neutralen Hydrodynamik-Kernels wie die CPU.
Die Architekturmanifestdatei des Solvers wird unverändert verwendet; sie enthält
auch SM 89 für die vorhandene RTX 4070 SUPER.

## Nachweise und verbleibende Schritte

- Toolchain-Protokoll: `validation/gaia026/hydrodynamics/cuda-toolchain.log`.
- Build-Protokoll: `validation/gaia026/hydrodynamics/cuda-build.log`.
- Alle 89 CUDA-Kompilierungsjobs, DLL-/Importbibliotheks-Link und der Rust-Release-Build
  sind erfolgreich. Für den Rust-Build wurden 9 Minuten 33 Sekunden gemessen,
  zusätzlich zum vorherigen DLL-Bootstrap.
- `cuda-probe.log` bestätigt `backend: cuda`, `linked: cuda` und
  `device: NVIDIA GeForce RTX 4070 SUPER`.
- `cuda-force-matrix-test-unit.log`: ein tatsächlich ausgeführter GPU-Test bestanden.
  Er installiert native Velocity-/SDF-Bricks, assembliert Normal- und Tangentialkräfte,
  liest Kraftvektor und alle neun 3×3-Matrixblöcke vom GPU-Backend zurück und vergleicht
  mit der Host-Assemblierung. Toleranz Kraft: `1e-4 + 1e-5*abs(Referenz)`;
  Matrix: `1e-3 + 1e-5*abs(Referenz)`. Die explizite Unit-Test-Auswahl verhindert,
  dass Cargo eine parallel laufende Simulations-EXE unter Windows ersetzen muss.
- Echter NEON-GPU-Pilot: drei Frames, ursprüngliche Materialien, Zonen, Attachments
  und Collider, 31,937 Sekunden; sämtliche ausgegebenen Positionen endlich.
  Bericht: `validation/gaia026/hydrodynamics/neon-spatial-cuda-pilot3/report.json`;
  native Kraft- und Kontaktmetriken: `numerical-summary.json` im selben Verzeichnis.
- 20 vollständige kleine GPU-Referenzsimulationen bestanden
  (`spatial-cuda-complete/report.json`). Enthalten sind Trockenheit, Teilbenetzung,
  räumliche/zeitliche Variation, bewegter Stoff, Dichte/Biegung, Netz-/Zeitschritt-
  Variation, statischer/animierter Collider, Selbstkontakt und Tangentialwiderstand.
- Der CPU-/CUDA-Vergleich der vollständigen kleinen Trajektorien unterschreitet
  in allen 20 Fällen die vorab gesetzte Positionsgrenze von 10 µm; das größte
  gemessene Positionsdelta beträgt 4,995 µm. Die zusätzliche Kraftgrenze
  `1e-5 N + 0.001*abs(Netto-Referenzkraft)` besteht in 19 von 20 Fällen.
  Der zusammengefasste strenge Vergleich bleibt deshalb **FAIL**
  (`spatial-cpu-cuda-comparison.json`); die Toleranzen wurden nicht erweitert.
- Im Selbstkontaktfall beträgt das Lastdelta 0,002166 N: CPU 0,201134 N gegenüber
  CUDA 0,198968 N, ungefähr 1,08 %. Die Positionsabweichung beträgt 4,322 µm.
  Beide Backends werten bei `t=0.100000009 s`, `dt=0.000500000024 s` aus;
  unterschiedliche Zeitstempel erklären diesen Unterschied nicht. Die letzten
  Kontaktmengen unterscheiden sich (CPU 19, CUDA 18). Das ist ein Hinweis auf
  unterschiedliche Kontakt-/Zustandspfade, aber noch kein abschließender
  Ursachennachweis. Der direkte Kraft-/Matrixvergleich auf demselben Zustand
  besteht separat. Die verbleibende Kontakt-Parität ist ausdrücklich offen.
  Eine gezielte zweite GPU-Selbstkontakt-Simulation besteht ebenfalls ihre
  Szenarioprüfung, liefert aber etwa 0,199222 N. Die Abweichung zur CPU bleibt
  damit auch in dieser Wiederholung außerhalb der Kraftgrenze.
  Nachweis: `self-contact-parity-investigation.json`.
- Der NEON-Vergleich mit 30 Frames ist auf CUDA für Flow einmal sowie Dry und
  Hydrodynamik jeweils zweimal abgeschlossen. Dieselben fünf CPU-Fälle und der
  große Backend-Trajektorienvergleich sind ebenfalls abgeschlossen (Details unten).
- Ein erfolgreicher Build allein gilt ausdrücklich nicht als Physik- oder CUDA-Validierung.

## Vollständige NEON-GPU-Ergebnisse

Die folgenden Messungen stammen aus tatsächlich abgeschlossenen nativen Läufen,
nicht aus nachträglich geschätzten Host-Kräften. Es werden die 30 Ausgabeframes
0–29 bei 30 FPS verwendet; sie entsprechen den FLIP-Quellframes 1–30 mit Frame 31
als Interpolations-Lookahead. Alle Varianten verwenden dieselben ursprünglichen
Materialien, Face-Zonen, 186 Attachments und Körper-/Kopf-Collider. Gemeinsame
Konvergenzparameter sind mindestens vier Newton-Schritte, `max-dx=0.001`,
`cg-tol=0.0001`, `dt=0.01` und Block-Jacobi.

| Lauf | Umfang | Laufzeit inklusive Ergebnissicherung |
| --- | --- | --- |
| Hydrodynamik 1 | 30 Frames, 77.860 Vertices | 99,11 s |
| Dry 1 | 30 Frames, 77.860 Vertices | 82,78 s |
| bisheriger Flow | 30 Frames, 77.860 Vertices | 86,94 s |
| Hydrodynamik 2 | 30 Frames, 77.860 Vertices | 102,31 s |
| Dry 2 | 30 Frames, 77.860 Vertices | 81,88 s |

Beim Hauptstoff beträgt der Unterschied zu Dry über alle Frames und Vertices:

| Modell | RMS | Maximum |
| --- | --- | --- |
| bisheriger Flow | 0,169 mm | 2,439 mm |
| native Hydrodynamik | 9,687 mm | 53,369 mm |

Diese Messung gilt für das experimentelle Widerstandsmodell mit `Cn=1`, `Ct=0`
und Wasserdichte 1000 kg/m³. Sie ist keine universelle Materialkalibrierung.
Die ursprünglichen Stoffmaterialien wurden nicht weicher gestellt.

Am letzten nativen Kraftauswertungszeitpunkt (`t=0.975633 s`, abweichend vom
interpolierten Ausgabeframe) hat der Hauptstoff etwa 0,60038 m² benetzte Fläche.
Die Summe lokaler Normalkraftbeträge beträgt 12,8065 N; die vektorielle Summe ist
ungefähr `[-8.3600, 0.09886, -4.20762] N`. Das zweite Kleidungsstück `.001` bleibt
trocken und hat keine direkte hydrodynamische Last. `.002` hat etwa 0,04011 m²
benetzte Fläche und 0,41844 N lokale Normalkraftbeträge.

Alle fünf GPU-Trajektorien sind vollständig endlich und haben native
`finished.txt`-Marker. Es wurden keine
nativen Kontakt-/CCD-Guard-Fehler gemeldet. Das ist kein unabhängiger Nachweis
gegen sämtliche Körperdurchdringungen. Die exportierten Kleidungs-Trajektorien
bleiben im vorbereiteten SDF-Ausschnitt; Zwischeniterationen des Newton-Verfahrens
werden von diesem nachträglichen Ausschnitt-Check nicht erfasst.

Die Hydrodynamik-Wiederholung zeigt beim Hauptstoff 0,222 mm RMS und 7,691 mm
maximale lokale Abweichung. Der RMS-Modellunterschied zu Dry ist damit etwa
44-mal größer als diese Wiederholungsstreuung. Bitidentische Reproduzierbarkeit
ist nicht erreicht; die lokale Streuung muss für eine Produktfreigabe untersucht
und mit einem vorher festgelegten Akzeptanzkriterium bewertet werden.
Die Dry-Wiederholung streut beim Hauptstoff um 0,00661 mm RMS und lokal maximal
1,114 mm. Die unterschiedliche Wiederholungsstreuung muss bei der Interpretation
der Modellvergleiche berücksichtigt werden.

Die native Streaming-Telemetrie meldet 31 gelesene Frames je Rolle, trotz vieler
Newton-Iterationen, mit zwei residenten Geräteframes. Die gemeldete Brick-Kapazität
beträgt 72.024.064 Bytes auf dem Gerät und 108.036.096 Bytes für den Host-Framecache.
Das sind Kapazitäten dieses Transports, nicht der gesamte Solver-Speicherbedarf.

Die Addon-Capability bleibt deaktiviert. Offene Produktionsfragen umfassen die
lokale Wiederholungsstreuung, die Voxel-SDF-Genauigkeit für dünne Wasserstrukturen,
eine harte native Prüfung bei Verlassen des vorbereiteten SDF-Ausschnitts und
eine über die unten dokumentierte Ausgabeframe-Schnittprüfung hinausgehende
Durchdringungsbewertung. Das Modell bildet dynamischen
Strömungswiderstand ab; hydrostatischer Druck/Auftrieb ist damit nicht validiert.

## CPU-Referenz und Herkunft der Wasseroberfläche

Die bisherigen nativen CPU-Regressionsprüfungen bestehen: 579 Tests bestanden,
neun ignoriert, kein Fehler (`spatial-native-regressions-workspace.log`).
Zusätzlich bestehen 37 Addon-/Transporttests (`spatial-addon-regressions.log`),
acht native Fehlerfalltests (`transport-refusals.json`) und 20 vollständige kleine
native CPU-Referenzsimulationen (`spatial-cpu-complete/report.json`).
Die Fehlerfälle prüfen unter anderem falsche Version/Rollen/Einheiten, ungleiche
Clocks, fehlende/beschädigte Frames, nichtendliche Geschwindigkeit und doppelte
Kopplung mit dem bisherigen Fluid Flow.
Nach der Fortsetzung wurden alle 165 relevanten GAIA-/Hydrodynamik-Addon-Tests
erneut ausgeführt: 165 bestanden, kein Fehler (`resume-addon-regressions.log`).

Die experimentelle SDF wird aus den vollständigen geschlossenen BOBJ-Oberflächen
erstellt, mit geprüftem Winding, SI-Transformation und versioniertem Manifest.
Die NEON-Zellweite beträgt etwa 7,826 mm. Der Ausschnitt wird frameweise aufgebaut;
es werden keine Oberflächen ausgedünnt und keine Originaldateien verändert.
Die Voxel-Darstellung kann dennoch kleinere Wasserstrukturen verlieren.

Der unabhängige Benetzungscheck gegen BOBJ auf der vorhandenen Dry-Trajektorie
ergibt an Quellframe 30 für den Hauptstoff 0,602227 m² exakte und 0,601889 m²
SDF-benetzte Fläche; für `.002` 0,040674 m² gegenüber 0,039634 m².
Diese Flächenprüfung ist keine zusätzliche hydrodynamische Simulation.
Nachweis: `surface-validation-crop4.json`.

Die fünf laufenden CPU-NEON-Läufe wurden zunächst auf ausdrücklichen Nutzerwunsch
gestoppt und nach erneutem Startauftrag aus ihren Frame-19-Checkpoints fortgesetzt.
Dafür wurden vollständige separate Session-Kopien angelegt. Die ursprünglichen
Teilausgaben bleiben erhalten. Alle fünf fortgesetzten Fälle haben native
Abschlussmarker, 30 endliche Positionsframes und keine gemeldeten Kontakt-/CCD-
Guard-Fehler. Die Statistik und Positionsprüfung umfasst die gesamte Trajektorie;
die neuen Laufzeitmessungen und nativen Logs erfassen nur den fortgesetzten Teil.
Provenienz: `neon-spatial-cpu-resumed-r1/report.json` und fallweise `resume.json`.

## Vollständiger CPU-/CUDA-NEON-Vergleich

Die RMS-Werte umfassen alle 30 Ausgabeframes und alle Vertices des Hauptstoffs.

| Vergleich beim Hauptstoff | CPU: RMS / Maximum | CUDA: RMS / Maximum |
| --- | --- | --- |
| Flow gegenüber Dry | 0,151 / 1,475 mm | 0,169 / 2,439 mm |
| Hydrodynamik gegenüber Dry | 9,685 / 53,295 mm | 9,687 / 53,369 mm |
| Hydro-Wiederholung | 0,265 / 9,353 mm | 0,222 / 7,691 mm |

Die Hauptstoff-Trajektorien von CPU und CUDA unterscheiden sich bei Hydro 1
um 0,242 mm RMS und maximal 8,480 mm, bei Hydro 2 um 0,174 mm RMS und maximal
6,232 mm. Bei Dry/Flow liegen die RMS-Backend-Differenzen zwischen 0,017 und
0,039 mm. Diese Messwerte sind keine nachträglich eingeführten Freigabetoleranzen.
Die starke Änderung gegenüber Dry ist auf beiden Backends praktisch gleich groß;
lokale Kontakt-/Faltenverläufe sind weiterhin nicht bitidentisch reproduzierbar.
Nachweise: `neon-cpu-cuda-comparison.json` und die jeweiligen `numerical-summary.json`.

![Native CPU-/CUDA-Trajektorien und Kräfte](validation/gaia026/hydrodynamics/plots/neon-cpu-cuda-validation.png)

Die Grafik zeigt oben die RMS-Abweichung zu Dry pro Ausgabeframe und unten die
native Last zum tatsächlichen Kraftauswertungszeitpunkt. `.001` ist direkt trocken;
kleine Bewegungsunterschiede können über Kontakte/Attachments vermittelt werden.

## Eingrenzung der Selbstkontakt-Abweichung

Ein zweiter tatsächlicher GPU-Test verwendet eine aus dem CPU-Selbstkontaktfall
übernommene Triangle-Geometrie und **vorgegebene gemeinsame** Startposen/Geschwindigkeiten
bei `dt=0.0005 s` und Strömung 0,2 m/s. Die Startposen rekonstruieren ausdrücklich
nicht den unexportierten internen Originalzustand. Bei diesen identischen Eingaben
sind GPU-/CPU-Kraftkomponenten und Matrixeinträge bitgleich (maximales Delta null).
Beide direkten GPU-Kraft-/Matrixtests bestehen:
`cuda-frozen-contact-scale-test-retry.log`.

Im ursprünglichen vollständigen Selbstkontaktlauf ist die native mittlere
Relativgeschwindigkeit dagegen 0,200564 m/s auf CPU und 0,199482 m/s auf CUDA.
Die nahezu gleiche Fläche und das quadratische Widerstandsgesetz erklären damit
fast das gesamte Lastdelta: aus der mittleren Geschwindigkeit näherungsweise
0,201130 N beziehungsweise 0,198965 N, gegenüber nativ 0,201134/0,198968 N.
Das spricht für eine zustandsabhängige Lastabweichung und gegen eine andere
Kraftskalierung. Es beweist keine abschließende Ursache der verschiedenen
Kontaktverläufe. Der ursprüngliche strenge Vergleich bleibt deshalb **FAIL**.

Ein zusätzlicher lastfreier Kontakt-Kontrollversuch mit bewegter Stofflage und
separatem festem Gegenmesh besteht auf beiden Backends. Der kurze Stoß wird mit
2000-Hz-Ausgabe erfasst; beide melden maximal 33 Kontakte. Die größte Positions-
abweichung beträgt 0,105 µm, das Kraftdelta ist null (Hydro-Koeffizienten null).
Dieser separate statische Gegenmesh-Test ist kein Ersatz für das ursprüngliche
Selbstkontakt-Szenario. Nachweis: `self-inertia-comparison.json`.

## Unabhängige Oberflächenschnittprüfung

Zusätzlich zur nativen CCD wurden **alle 300 ausgegebenen Frames** der fünf CPU-
und fünf CUDA-Fälle geprüft. Cloth-Kanten wurden gegen Körper-/Kopf-Triangles und
umgekehrt Körperkanten gegen Cloth-Triangles geprüft. Ergebnis: **null schneidende
Segmente**, Endpunktausschluss 1 µm. Ein analytischer Kontrollfall prüft einen
bekannten Schnitt, einen Miss, ein zu kurzes Segment und einen ausgeschlossenen
Endpunkttreffer vor der Auswertung.

Die Collider-Meshes sind nicht geschlossen. Dieser Test behauptet daher keine
globale Innen-/Außen-Klassifikation und keine zusätzliche kontinuierliche Prüfung
zwischen Ausgabeframes. Nachweise: jeweils `collider-crossing-audit.json` in
`neon-spatial-cuda30` und `neon-spatial-cpu-resumed-r1`. Auch die vollständigen
CPU-Ausgabetrajektorien bleiben innerhalb des vorbereiteten SDF-Ausschnitts.

## Bewertung

**Experimenteller nativer CPU-/CUDA-Datenfluss und reale NEON-Kopplung nachgewiesen.**
Die Kopplung ist flächen- und relativgeschwindigkeitsabhängig und verändert die
NEON-Trajektorie bei unveränderten ursprünglichen Materialien deutlich. Die
Modellreaktion stimmt zwischen CPU und CUDA weitgehend überein.

**Keine Produktfreigabe.** Offen sind das strenge zustandsabhängige Kraftkriterium,
die lokale Wiederholungsstreuung, die native Absicherung bei Verlassen eines
SDF-Ausschnitts und eine Freigabe-/Blender-Lifecycle-Prüfung. Die Addon-Capability
bleibt deaktiviert; es wurde kein offizieller Solver ersetzt oder veröffentlicht.

Der gesicherte native Quellcode-Patch ist
`validation/gaia026/hydrodynamics/native-spatial-hydrodynamics.patch`;
Basiscommit und Prüfsummen der CPU-/CUDA-EXE sowie der CUDA-DLL stehen in
`cuda-build-evidence.json` im selben Verzeichnis. Es wurden keine Solver-Binaries
in dieses Addon-Repository kopiert, keine Commits angelegt und nichts veröffentlicht.
