# GAIA — Rekonstruktion und NEON 130–159: Ergebnis

Stand: **17.10.2026**. **Der experimentelle reguläre CUDA-Blender-Bake 130–159
mit echtem160-Lookahead ist bestanden.** Geometrie, physikalische Antwort,
visueller Verlauf und numerische Wiederholbarkeit werden getrennt bewertet.
Die Wasserrekonstruktion wird nicht wegen eines2,5mm-Vertexabstands verworfen.
**Keine Release-Freigabe:** die Trajektorien-Wiederholbarkeit bleibt offen.

## 1. Ursache von130 und135

Der ursprüngliche FLIP-Mesher verbindet drei Rechenblöcke über einen
Abstandsvergleich. Eine Diagnosekopie des originalen Pair-Finders weist echte
Fehlzuordnungen zwischen unterschiedlichen globalen Gitterkanten nach:
drei in130, zwei in135. Sie liegen an den BlocknähtenY170/340,
weltweit ungefährY=-0,6571/+0,7054m.
Der Original-Algorithmus reproduziert die defekten Quellen:
130:7offene/1nicht-manifold Kante;135:2offene/2nicht-manifold Kanten.
Getrennte geschlossene Wasserkörper sind gültig; sie werden erhalten.

## 2. Gewählte Methode

Verwendet wird der mitgelieferte **FLIP1.8.8 Individual-Quellmesher** mit
vollständigen originalen Partikeln, ursprünglichem statischem Fluid-Collider,
Originalradius, drei Blöcken, ursprünglicher Auflösung und Glättung.
Die Verbindung erfolgt über die tatsächliche globale Gitterkante
(Gitterursprung+Achse), statt unterschiedliche Kanten per Nähe zu verschweißen.
Keine Lochkappen, neuen Triangles, Radiusänderung oder entfernten Wasserteile.
Alle Partikel/Velocity-Eingaben und der Zustand vor/nach Meshing werden exakt geprüft.
Kein Simulationsfortschritt: der öffentliche `update(0)`-Ansatz wurde an einem
synthetischen Test verworfen und niemals auf NEON angewendet.

Cache/Engine:1.8.8 Individual,27.07.2026/759LY. Das Quellarchiv trägt dieselbe
Version/Datum, aber einen anderen Build-Identifier539NT; Compiler15.2 statt15.0.
Es wird keine Binäridentität behauptet. Ein gespeichertes originales Liquid-SDF
wurde nicht gefunden. Der Originalmesher rekonstruiert aus verifizierten Partikeln.

Die Mesher-Vorbereitung läuft auf CPU; **die Cloth-Hydrodynamik läuft im vorhandenen
nativen CUDA-Solver**. Keine zweite Kraftimplementierung, keine Änderung am
Solver oder Kraftmodell in dieser Untersuchung. Das geschlossene Ergebnis wird
über die vorhandene Velocity-/Signed-EDT-Transportpipeline eingespeist.
Die Einbindung ist explizit, hashgebunden und auf den isolierten NEON-Test beschränkt.

Alle31Frames130–160 sind geschlossen und konsistent orientiert, bei unveränderten
Triangle- und Komponentenzahlen.149/151 besitzen jeweils einen zusammengeführten
Nahtvertex weniger; Original-Replay und unabhängige Belegungskontrolle sind geprüft.
157 behält dieselben vier flächenlosen Triangles wie das Original; sie wurden nicht
still entfernt. Es gibt keine Forderung nach einer einzigen Wasserkomponente.

## 3. Fehlermaße und Auflösungsgrenzen

- FLIP-Zelle16,0284mm, ursprüngliches Oberflächengitter8,0142mm.
- Der diskutierte Wert2,49165mm ist ein **beidseitiger Nächster-Vertex-Abstand**
  zu einem defekten BOBJ, kein Hausdorff-Abstand und kein Feldresiduum.
- Lokal abgetastete Punkt-zu-Triangle-Abstände beobachten maximal1,038mm in130
  und0,407mm in135.10Samples/Triangle,30mm erweiterter Problembereich.
  Keine garantierten globalen Hausdorff-Grenzen; ein defektes BOBJ ist keine
  unabhängige physikalische Wahrheit.
- Verwendeter Transport:7,82636mm-Zellen im geprüften Cloth-Umfeld. Außerhalb
  des transportierten Gebiets bleiben Kraftauswertungen durch den nativen Guard verboten.
  Das ist eine begrenzte Kraftrepräsentation, kein vollständiger Domain-SDF-Export.
- Subzell-Strukturen können bei der Voxelisierung verloren gehen. Beispiel130:
  219geschlossene Komponenten haben alle Bounding-Box-Achsen kleiner als eine
  Transportzelle; ihr summiertes absolutes Meshvolumen beträgt etwa0,207ml.
  Das sind Risikokandidaten, **keine Messung verlorenen Wassers**. Alle Komponenten
  bleiben im rekonstruierten Gesamtmesh erhalten; auch dünne Bereiche größerer
  Komponenten können durch die Abtastung unaufgelöst bleiben.

Die Sensitivitätsvarianten addieren±0,0025m zum **gespeicherten Signed-EDT-Wert**.
Das ist keine garantierte geometrische Oberflächenverschiebung um exakt2,5mm:
die trilineare Voxelrepräsentation ist keine exakte metrische Distanzfunktion.
Die Versuche messen Feld-/Benetzungsempfindlichkeit. Frühere Formulierungen einer
„konservativen2,5mm-Verschiebung“ sind entsprechend einzugrenzen; ein geometrischer
±2,5mm-Abstand wird dadurch nicht zertifiziert. Keine dieser Varianten wurde
als eigentliche Wasserrepräsentation übernommen.

## 4. Referenz131–133 und unabhängige Fluidkontrolle

131–133 werden mit identischer Triangle-Konnektivität reproduziert;
maximale Vertexabweichungen2,536/2,325/5,066µm. Der historische10µm-Test bleibt
als Reproduktionsdiagnose erhalten und ist kein pauschales physikalisches NO-GO.
Für131–134 sind **alle18Velocity-/SDF-Payloads einschließlich Transformationen
bitgleich** zum bisherigen erfolgreichen Referenzlauf. Damit sind Benetzung und
native Kraftauswertung bei identischem Solverzustand unverändert.

Auf drei Cloths über131–133 besteht das feine SDF alle9flächengewichteten
Belegungsprüfungen gegen gültige geschlossene BOBJ-Referenzen.15,653mm besteht
beim kleineren benetzten Kleidungsstück nicht und wird nicht übernommen.

Für130/135 und149/151 wurde zusätzlich der unveränderte Originalmesher ohne
Block-Join als unabhängige Partitionskontrolle verwendet. Je100000deterministische
Punkte nahe eingefrorenem Referenz-Cloth:0Belegungsunterschiede; identische
Dreipunkt-Benetzung auf allen drei Cloths. Das ist eine lokale Stichprobenprüfung
auf eingefrorener131-Geometrie, kein Beweis über den ganzen Raum oder die späteren
130/135/149/151-Trajektorien. Originale Particle-/Velocity-Daten sind vollständig,
endlich und unverändert; Partikelradius-Residuals werden nur als Supportdiagnose
interpretiert, nicht als geometrische Oberflächenfehler.

## 5. Echte Blender-Bakes und Regressionen

| Test | Ergebnis |
|---|---|
| Separater1–30+echter31 | PASS; kein Ersatz für130–159 |
| Kurzer rekonstruierter130–132+echter133 | PASS;83,42s Operator |
| Voller rekonstruierter130–159+echter160 | **PASS;438,29s Operator** |
| Frischer Blender-Prozess:alle30PC2-Samples,alle3Cloths | PASS |
| Native→PC2 maximal |6,104e-8m |
| Reload-Playback gegen PC2 | exakt0Abweichung |
| Unabhängiger reziproker Collider-Test,alle30Frames |0kreuzende Segmente |
| SDF-Abdeckung der ausgegebenen Cloth-Vertices | Mindestabstand0,22102m |
| Native SDF-/Velocity-Guards an Kraftauswertungen | aktiv,kein Fehler |
| Gezielte Python-Regressionen |32PASS,inklusive6Source-Binding-Tests |

Drei Cloths mit23356Vertices, Originalmaterialien/Zonen und186Attachments,
Cn=1,Ct=0,rho=1000kg/m³,One-Way Coupling und kein paralleler alter Fluid Flow.
Modell ist der vorhandene dynamische Sheet-Drag-Term; keine Behauptung einer
hydrostatischen oder vollständig gekoppelten FLIP-Drucklösung.

Die Collider-Prüfung betrifft exportierte Frames bei1µm-Endpunktausschluss.
Offene Collider erlauben keinen pauschalen globalen Innen-/Außen-Nachweis;
Zwischenframe-Kollisionen sind damit nicht unabhängig bewiesen.
Gemessene Ressourcen:117Stichproben im5s-Takt;maximal10,175GB Prozessbaum-RSS,
12,003GB private Bytes und5154MiB globale GPU-Belegung. Keine garantierten
Lebenszeit-Peaks; GPU-Messung umfasst andere Anwendungen.

Die früheren nativen CPU-Regressionen und eingefrorenen CPU-/CUDA-Arbeiten bleiben
erhalten (u.a.579CPU-Tests laut bestehendem CPU-Bericht). Es gab keine neue
CPU-Physikänderung. Der bestehende strenge CPU-/CUDA-Kraftvergleich bleibt19/20:
Selbstkontakt-Parität ist weiterhin offen. Dieser Befund wird nicht aufgehoben.

## 6. Kräfte, Benetzung und zeitliche Sensitivität

29native Kraftausgaben: normale Lastsumme90,975–143,666N, benetzte Fläche
0,53776–0,67302m², tangentiale Last0. Alle Werte sind endlich und mit der
nativen Drag-Obergrenze konsistent. Es wurden keine Host-Kräfte nachträglich
auf die Trajektorie angewendet.

Vier Übergänge überschreiten den vorab gesetzten10%-Prüfhinweis. Der Verlauf
zeigt eine mehrframeige Spitze, keine isoliert wechselnde Einzelspitze.
Zusätzlich zum echten Operatorlauf wurden vier vollständige30-Frame-Replays
mit unveränderten Originalparametern ausgeführt:zweimal unverändert,
Signed-EDT+2,5mm und-2,5mm. Alle vier beenden sich regulär.
Die Lastspitze liegt in **jedem** Lauf beiFrame152; Spitzensummen143,10–146,82N.
Sie geht mit dem gemessenen mehrframeigen Verlauf der Relativgeschwindigkeit
und Benetzung einher. Die Differenz der Kraftprofile und ihrer ersten Differenzen
bleibt in den vorab festgelegten physikalischen Prüfschranken.

Hauptstoff gegenüber Operatorreferenz:

| Eingabe | Benetzung:maxDelta | Last:maxDelta | Trajektorie:RMS / Max |
|---|---:|---:|---:|
| unverändert1 |0,00143m² |2,740N |1,331 /21,20mm |
| unverändert2 |0,00178m² |4,582N |1,261 /26,13mm |
| Signed-EDT+2,5mm |0,00469m² |3,463N |1,332 /14,02mm |
| Signed-EDT-2,5mm |0,00427m² |4,197N |1,497 /20,31mm |

Last- und Benetzungsscreens bestehen auf allen drei Cloths. Die verschobenen
Felder werden ausschließlich als Sensitivitätsexperimente ausgewertet, nicht
als vergrößertes oder verdünntes Produktwasser übernommen. Die zeitlichen
Prüfhinweise sind als reproduzierbare Profiltransienten eingeordnet; keine
meshingbedingte zufällige Einzelspitze nachgewiesen. Das zertifiziert weder
alle Newton-/Substep-Zustände noch allgemeine Stabilität für andere Szenen.

## 7. Visuelle Qualität und numerische Wiederholbarkeit

Alle30Frontalansichten und Anfang/Mitte/Ende von der Seite wurden diagnostisch
gerendert und geprüft. Der Formverlauf bleibt zusammenhängend, ohne beobachtete
Explosion oder groben Sprung. Es handelt sich um Falschfarbenansichten echter
nativer Geometrie, keinen Beauty-/Materialrender und keinen Vergleich mit realem Wasser.
Das GIF zeigt die30Hz-Simulation zur Inspektion verlangsamt mit10Bildern/s.

**Das8mm-Maximum-Ziel der Trajektorien-Reproduzierbarkeit ist nicht bestanden.**
Schon unveränderte Eingaben erreichen26,13mm Materialvertex-Abstand zur
Operatorreferenz bzw.22,59mm zwischen den beiden nativen Wiederholungen.
Bei der schlechtesten unveränderten Wiederholung sind im betreffenden Frame
1,67% der Hauptstoff-Vertices mehr als8mm versetzt. Die Abweichung ist nicht
rein tangential:am maximalen Vertex beträgt die Normalprojektion24,50mm;
abgetastete Punkt-zu-Triangle-Abstände beobachten dort maximal19,61mm.
Kein Hausdorff- oder Alle-Frames-Nachweis.

Damit ist die Trajektorien-Fehlerattribution zur Rekonstruktion **nicht eindeutig**.
Der Maximalscreen bleibt INVESTIGATE; er wird nicht nachträglich gelockert.
Stabile endliche Läufe, akzeptable Kraftsensitivität, visuelle Kohärenz und
numerische Wiederholbarkeit sind unterschiedliche Aussagen. Der Rohbefund
bleibt als numerischer Release-Blocker bestehen.

## 8. Quellenintegrität, Ergebnis und offene Blocker

93Originalpayloads (BOBJ,volle Partikel,Velocity) wurden nach dem Test gegen
vorgesicherte Hashes geprüft:unverändert. Die eingefrorene Blend-Referenz ist
weiterhin`d0a7055f…baa2437`, passend zum erfolgreichen131–133-Referenzlauf.
Die heutige Originaldatei hat`c22a2da5…15c0af3` und einen Änderungszeitpunkt
**16.10.2026,17:31:28Europe/Berlin**,vor den aktuellen Tests. Wer sie gespeichert
hat, ist nicht bestimmt. Die Testphase schreibt sie nicht; es wird keine
Übereinstimmung der getesteten Referenz mit diesem neueren Dateistand behauptet.
Keine Rücksetzung oder Überschreibung der Originaldatei.

**Ergebnis:** Der strukturelle Join-Fix ermöglicht den verlangten echten30-Frame-
Bake mit verifizierten Fluidquellen und bestandenen physikalischen Last-/Benetzungs-
Sensitivitätstests. Ein2,5mm-Nächster-Vertex-Abstand ist kein Ablehnungsgrund.
**Release:NO-GO** bis die unabhängigen numerischen/Integrationspunkte geklärt sind:

1. Trajektorien-Wiederholbarkeit und vorhandene CPU-/CUDA-Selbstkontakt-Parität.
2. Allgemeine, versionsgebundene Rekonstruktionsintegration/Distribution;
   derzeit expliziter NEON-spezifischer Experimental-Preparer mit eingefrorenen Quellen.
3. Übertragbarkeit auf den neueren Original-Blend-Stand und weitere Szenen;
   aktueller Nachweis gilt für die erhaltene Referenzkopie.
4. Weiterführende visuelle Material-/Szenenprüfung und längerfristige bzw.
   Zwischenframe-Validierung, soweit für die Freigabe gefordert.

Keine Versionsänderung,kein Commit,Push,Tag oder Release. Offizielle Solver-
Binaries bleiben unverändert; vorhandene experimentelle Solverarbeit und
sämtliche alten Regressionsergebnisse sind erhalten.

Messdaten:
`validation/gaia026/hydrodynamics/flip-original/` und
`validation/gaia026/hydrodynamics/blender-reconstructed-130-159/`.
Insbesondere`summary.json`,`native-temporal.json`,`long-native-sensitivity.json`,
`repeatability-localization.json`,`post-test-source-integrity.json` (imflip-original-
Ordner),Collider-Audit,`native-diagnostics.png`,`native-contact-sheet.png` und
`native-playback-10fps.gif`. Rohlogs/Eingaben bleiben unter`.hydro-test-tmp/`.
