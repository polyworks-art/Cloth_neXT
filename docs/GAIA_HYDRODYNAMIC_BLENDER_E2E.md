# GAIA Hydrodynamic Pressure — erster regulärer Blender-Bake

Stand: 16.10.2026. **Kurzer End-to-End-Test bestanden; der geforderte
30-Frame-Test im mittleren Abschnitt ist noch nicht bestanden.**

## Tatsächlicher Test

Cloth NeXt **2.9.25**, Blender **5.2.2 LTS**, RTX 4070 SUPER, CUDA.
Ausgangspunkt ist eine separate Kopie von
`F:/16_Cloth_neXt/SW04/SW04_NEON.blend`.
Alle drei Cloth-Objekte (14.946 / 4.379 / 4.031 Vertices), Originalmaterialien,
Material Zones, vier Attachment-Gruppen mit **186 Verbindungen**, Body/Head und
Animation bleiben erhalten. Die ursprüngliche Blend-Datei hat weiterhin
denselben SHA256 wie die unveränderte anfängliche Dateikopie.

Auf Nutzerwunsch liegt der Test in der Mitte der Animation. Frame 130 wurde
sicher abgelehnt: sieben offene und eine nicht-manifold Kante. Frame 135 hat
ebenfalls offene/nicht-manifold Kanten. Deshalb verwendet Test A **131–133**
(drei Ausgabeframes) plus den echten Fluid-Lookahead **134**.
Getrennte geschlossene Wasserteile sind ausdrücklich erlaubt: Frame 131 mit
**378 Komponenten** wird akzeptiert. Es gibt keine Forderung nach einem
einzigen zusammenhängenden Fluid-Mesh und keine automatische Lochfüllung.

Start über **`bpy.ops.clothnext.bake('EXEC_DEFAULT')`**, reguläre Vorbereitung,
Server, Upload, offizielles Frontend, native CUDA-Simulation und PC2-Import.
Der erfolgreiche Hydro-Operatorlauf benötigt etwa **77,1 s** einschließlich
Vorbereitung; ein separater kurzer Dry-Operatorlauf dient als Kontrolle.

## Isolation und Identität

Das Testprofil und Runtime-Bundle liegen ausschließlich unter
`.hydro-test-tmp/blender-e2e-20261016/` im Entwicklungscheckout. Es wird eine
eigene Registry über prozesslokales `LOCALAPPDATA` benutzt. Der vorhandene
gebündelte Python-Interpreter wird read-only wiederverwendet; Python-Bytecode-
Schreiben ist im Testprozess deaktiviert. Frontend, Control-Server und
Runtime-Dateien sind separate Kopien. Die offiziellen Binaries bleiben unverändert.

| Datei | SHA256 |
|---|---|
| experimenteller CUDA-Worker mit SDF-Guard | `bb10a7e97ced6f4a7b42637ad5822dd1546a70a3206ec0504a1ef6ef2e728a94` |
| CUDA-Backend-DLL mit SDF-Guard | `d325f4d65b94717f2807263cd9a3ea9e455d2cbab5b1be38258708f2606c6005` |
| kopierter Control-Server | `0d5fa78fb44e222d09b8760d4a602cf65f815edaa5096aa51998e61c3149c270` |

Die vorhandene native Hydrodynamik wurde wiederverwendet. Für die geforderte
SDF-Sicherheit war eine minimale Ergänzung nötig: Der native Kraftkernel
bricht bei fehlender SDF-Abdeckung auch für trockene Quadraturpunkte ab,
statt fehlende Daten als trocken zu behandeln. Keine Änderung des Kraftmodells.
Der zuvor getestete CUDA-DLL-Stand wurde vorher separat gesichert; die
eingefrorenen CPU-/CUDA-Worker bleiben erhalten.

Freischaltung nur mit explizitem Manifest innerhalb des isolierten
`BLENDER_USER_RESOURCES`-Profils, passenden Server-/Worker-/DLL-Hashes und
tatsächlicher Worker-Probe für CUDA, typisierten Velocity-/SDF-Transport und
`hydrodynamic-sdf-bounds: fail-closed-v1`. Protocol 0.26 allein schaltet nichts frei.
Der alte experimentelle Worker ohne Bounds-Guard wurde in echtem Blender
abgelehnt. Normale Profile bleiben gesperrt.

## Ergebnisse

Alle drei tatsächlichen PC2-Dateien enthalten drei Frames. Ein separater
Blender-Prozess hat die gespeicherte Testszene neu geladen und jede Probe
gegen native Vertex-Ausgaben und ausgewertete Blender-Geometrie geprüft.

| Objekt | Native → PC2, maximal in Weltmetern | Playback nach Reload, lokale Einheiten |
|---|---:|---:|
| S04_NEON_Garments | 3,052e-8 m | 0 |
| S04_NEON_Garments.001 | 6,102e-8 m | 0 |
| S04_NEON_Garments.002 | 6,079e-8 m | 0 |

Die PC2 speichert Float32-Objektkoordinaten; die originalen Objektmatrizen
haben Skalierung 0,001. Deshalb erfolgt der Vergleich zur nativen Ausgabe
in physikalischen Weltmetern. Die bestehende Grenze von 1e-5 m wurde nicht erhöht.

Native summierte normale Flächenlast: **98,868 / 106,324 N** in den beiden
simulierten Outputs. Benetzte Fläche: **0,64358 / 0,62193 m²**. Tangentiallast 0
bei Ct=0. Das sind Summen der Beträge der Flächenlasten, keine Nettokraftnorm.
Es gilt das bestehende native Sheet-Drag-Modell mit rho=1000 kg/m³, Cn=1, Ct=0;
keine hydrostatische Kraft und keine Rückwirkung auf FLIP.

| Objekt | Hydro vs. Dry RMS über drei PC2-Proben | Maximum |
|---|---:|---:|
| S04_NEON_Garments | 9,341 mm | **56,248 mm** |
| S04_NEON_Garments.001 | 0,206 mm | 1,109 mm |
| S04_NEON_Garments.002 | 0,490 mm | 5,312 mm |

Die früheren Referenzen für Frames 1–30 haben einen anderen Fluid-/Animations-
Zeitabschnitt und werden hier nicht als identische Referenztrajektorie behandelt.
Die dort dokumentierte CPU-/CUDA- und Wiederholungsstreuung bleibt offen.

SDF-Auflösung 7,826 mm; Bounds aus den evaluierten Cloth-Grenzen des Abschnitts
mit 0,25 m Reserve, explizite Achsentransformation und 30-Hz-Quellzeittakt.
Alle ausgegebenen Cloth-Vertices liegen innerhalb, kleinster Abstand ca.
**0,23055 m**. Zusätzlich prüft der native Guard die Kraftquadratur bei jedem
Iterat. Der gezielte native Test besteht: gültige SDF akzeptiert, verschobene
SDF mit Device-Diagnose abgelehnt. Keine stillschweigende Trockenklassifizierung.

Unabhängiger reziproker Kanten-/Dreiecks-Test in beiden simulierten Outputs:
**0 kreuzende Segmente**, Endpunktausschluss wie zuvor 1 µm. Die Collider sind
offene Oberflächen; dies ist keine globale Innen-/Außen- oder Zwischenframe-Aussage.

## Bedienung, Fehler und Grenzen

- Pressure-Aktivierung im echten GUI sichtbar und gesichert.
- Re-Bake im klassischen Simulation-Panel tatsächlich angeklickt; reguläre
  FLIP-Prüfung und native SDF-Vorbereitung erreicht.
- Während SDF-Vorbereitung über **Cancel Bake** abgebrochen: Zustand
  `CANCELLED`, kein Fehler. Alle drei bisherigen fertigen PC2-Dateien behalten
  ihren ursprünglichen SHA256. Neue native Simulation wurde dabei nicht gestartet.
- Timeline-Playback in der sichtbaren Blender-Testinstanz gestartet, wechselnde
  Frames/Cloth-Geometrie beobachtet und wieder pausiert.
- Native Daten und reguläre Projekt-/Cache-Ownership bleiben getrennt.
  Echte Windows-Junctions erfordern Ausführung außerhalb der Tool-Sandbox;
  die Sandbox hatte deren Erstellung mit Zugriff verweigert blockiert.
  Kein WinError 183 im erfolgreichen Operatorpfad beobachtet.
- Zeitabdeckung war ein nachgewiesener Integrationsfehler: ohne echten Lookahead
  stoppte die native Uhr bei 0,10322749 außerhalb [0; 0,10000001]. Dies wurde
  durch einen zusätzlichen echten Fluid-Frame behoben, ohne Clamping/Retiming.
- Die Endprüfung musste Windows-Langpfad-I/O und physikalische Weltkoordinaten
  verwenden. Die vorigen fehlgeschlagenen Harness-Berichte bleiben erhalten;
  der erfolgreiche Operatorzustand und die unabhängige Verifikation sind getrennt dokumentiert.
- Die klassische GUI ist für Re-Bake/Cancel geprüft. Floating-HUD-Klickverhalten,
  vollständiger GUI-ETA-Test bei langem Solve und GUI-Löschdialog sind nicht freigegeben.
  Während synchroner Blender-Capture war die Oberfläche vorübergehend träge.
- Save/Reload des PC2-Playbacks bestanden. Resume aus einem finalen Checkpoint
  ist hier nicht nachgewiesen; die Anzeige eines anfänglichen Recovery-Status
  ist kein Beleg für resumierbare Daten.
- **Test B 130–159 bleibt wegen ungültiger Quelloberflächen blockiert.** Es
  wurden keine Frames übersprungen, keine Oberflächen still repariert und
  keine Benetzungstoleranzen gelockert. Kein bestandener 30-Frame-End-to-End-Test.
- Bekannte Release-Blocker bleiben: strenger Selbstkontakt-Kraftvergleich 19/20,
  lokale Wiederholungsstreuung und vollständige 30-Frame-/GUI-Abnahme.

## Belege

357 relevante Add-on-Tests bestanden, keine neuen Skips:
[`addon-tests.log`](validation/gaia026/hydrodynamics/blender-e2e/addon-tests.log).
Maschinenlesbar: [`summary.json`](validation/gaia026/hydrodynamics/blender-e2e/summary.json),
[`hydro-verification.json`](validation/gaia026/hydrodynamics/blender-e2e/hydro-verification.json),
[`native-bounds.json`](validation/gaia026/hydrodynamics/blender-e2e/native-bounds.json),
[`Collider-Audit`](validation/gaia026/hydrodynamics/blender-e2e/collider-audit/collider-crossing-audit.json).

![Pressure im tatsächlichen Blender-GUI](validation/gaia026/hydrodynamics/blender-pressure-ui.png)
![Tatsächliches PC2-Timeline-Playback](validation/gaia026/hydrodynamics/blender-pc2-playback-ui.png)

Keine Versionsänderung, kein Commit, Tag, Push oder Release.
