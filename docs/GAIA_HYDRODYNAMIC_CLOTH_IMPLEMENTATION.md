# GAIA: hydrodynamische Cloth-Lasten — experimentelle Implementierung

Stand: 15. Oktober 2026. Keine Versionserhöhung, kein Commit, keine Veröffentlichung.

Fortsetzung vom 16. Oktober: Die native räumliche FLIP-/SDF-Anbindung und echte
NEON-GPU-Läufe sind inzwischen implementiert und ausgeführt. Aktueller Stand:
[CUDA- und NEON-Validierung](GAIA_HYDRODYNAMIC_CUDA_VALIDATION.md) und
[nativer Datenfluss](GAIA_HYDRODYNAMIC_DATA_FLOW.md). Die folgenden Abschnitte
dokumentieren den damaligen Stand vom 15. Oktober.

## Ergebnis und Ausführungsgrenze

Eine eigenständige SI-Referenz und eine **tatsächlich ausgeführte native CPU-Erweiterung** sind implementiert. Der native Term sitzt in der Kraft-/Matrixassemblierung des Newton-Solvers. Er verwendet aktuelle Triangle-Flächen und Normalen, Relativgeschwindigkeit, Wasserflächendeckung und eine getrennte physikalische Flüssigkeitsdichte. Keine nachträglichen Positions- oder Geschwindigkeitskorrekturen.

**Noch keine fertige NEON-Drucksimulation:** Der native experimentelle Transport unterstützt vorgegebene gleichförmige Strömung und bekannte Benetzung. Räumlich/zeitlich aufgelöste FLIP-Geschwindigkeit plus Oberflächenbenetzung sind noch nicht an diesen nativen Term angeschlossen. Für NEON sind die neuen Newton-Kräfte daher nur offline auf vorhandenen trockenen Trajektorien geschätzt. Eine neue hydrodynamische NEON-Trajektorie, deren Wet-/Dry-Differenz und native Kontaktreaktionskräfte wurden nicht gemessen. Diese fehlenden Integrationsschritte dürfen nicht als bestanden gelten.

Die vorbereitete Addon-Anzeige **Hydrodynamic Pressure** bleibt deaktiviert. Protokollnummer 0.26 oder vorhandenes FluidVolume reichen ausdrücklich nicht als Capability-Nachweis.

## Bestehende Architektur

Die vorherige Untersuchung einschließlich Material-, Kontakt-, Transformations- und Quellprüfungen steht in `GAIA026_NEON_COUPLING_INVESTIGATION.md`.

Der offizielle 0.26-Term ist pro Vertex, schematisch:

```
a_i = coupling_1_s * field_weight_i * occupancy(x_i,t)
      * (fluid_velocity(x_i,t)/playback - flow * cloth_velocity_i)
F_i = mass_i * a_i
```

Der Solver berücksichtigt diesen geschwindigkeitsabhängigen Term implizit, einschließlich einer positiven Dämpfungsableitung. Fluid-Spatialderivate werden dabei nicht vollständig differenziert. Triangle-Fläche, aktuelle Triangle-Normale und ein Druckkoeffizient gehören nicht zu dieser Kraft. Eine höhere Coupling Strength ist keine flächenbasierte Drucksimulation.

Native FluidVolume enthält zeitlich interpolierte sparse Bricks für Geschwindigkeit und einen skalaren Kanal. Der Cloth-NeXt-Adapter schreibt dort Partikel-Support/Occupancy hinein. Dieser Kanal ist weder kg/m³ noch ein gemessener Druck noch eine exakte Flüssigkeits-Volumenfraktion.

Materialmasse entsteht aus Flächendichte und Restfläche. Die normalen Materialparameter sind teilweise massennormiert; Wasserkräfte dürfen deshalb nicht durch die native materialseitige Young-/Bend-Normierung dividiert werden. Kontakt-, Reibungs-, Strain-Limit-, Pin- und Body-Mechanik bleiben beim bestehenden Solver.

Offizielle Feldskripte bekommen Raum/Zeit, aber keine lokale Cloth-Triangle-Geometrie und keine geeignete Triangle-Assemblierungs-API. Der bestehende native Materialparameter `pressure` ist Inflationsdruck, kein flussabhängiger Wasserterm. Das offizielle CBOR/PARAM-Protokoll kann dieses neue Flächenmodell nicht korrekt ausdrücken. Der experimentelle JSON-Testeingang ist ausschließlich eine neue Schnittstelle im separaten Entwicklungsbuild, keine behauptete GAIA-Funktion.

## Modell und Einheiten

Für die aktuelle Triangle-Fläche A und Einheitsnormale n:

```
r = u - v_bar                         [m/s]
s = dot(n,r)                          [m/s]
t = r - s*n                           [m/s]
A_w = A * wet_area_fraction           [m²]
F = 0.5 * rho * A_w * (Cn*abs(s)*s*n + Ct*norm(t)*t)  [N]
q = 0.5 * rho * dot(r,r)               [Pa]
```

`q` allein ist keine Kraft. Der Normalanteil verwendet den normalen Geschwindigkeitsanteil und skaliert bei konstantem Betrag mit cos² des Auftreffwinkels. Er ist zweiseitig und unabhängig von der Triangle-Winding-Richtung. Der Tangentialanteil ist separat; Ct=0 entspricht hier einer reibungsfreien tangentialen Grenzannahme, nicht dem Nachweis, dass reale Textilien keinen Schubwiderstand besitzen.

Cn=1 ist eine **offengelegte Modellannahme für eine undurchlässige lokale Fläche**, kein universeller oder gemessener Textil-Widerstandskoeffizient. Textilporosität, Durchströmung, Reynolds-Zahl und Grenzschichtzustand sind noch nicht kalibriert. Ct muss aus einem begründeten Material-/Strömungsmodell oder Messungen stammen. Die Referenz nimmt rho=1000 kg/m³ an; eine tatsächliche Materialdichte ist aus den untersuchten Cache-Dateien nicht ablesbar. Diese Eingaben sind physikalische Modellparameter, keine Artist-Strength-Multiplikatoren.

Grundlage für Druck-/Flächen-/Geschwindigkeitsskalierung: [NASA Drag Equation](https://www1.grc.nasa.gov/beginners-guide-to-aeronautics/drag-equation/). Die Grenzen eines universellen Koeffizienten erläutert [NASA Drag Coefficient](https://www1.grc.nasa.gov/beginners-guide-to-aeronautics/drag-coefficient/). Die hier verwendete Normal-/Tangentialaufteilung ist eine eigene, explizite Closure; NASA bestätigt nicht deren Gültigkeit für das konkrete NEON-Gewebe.

Die SI-Referenz verteilt die Centroid-Kraft mit F_i=F/3. Die gekoppelten Ableitungen berücksichtigen, dass v_bar=(v0+v1+v2)/3:

```
D = rho*A_w*Cn*abs(s)*n*n^T
    + 0.5*rho*A_w*Ct*(norm(t)*(I-n*n^T) + t*t^T/norm(t))
-dF_i/dv_j = D/9
H_ij = D/(9*dt)
```

Der Tangentialterm entfällt im Grenzfall norm(t)=0. D ist positiv semidefinit. Alle neun 3x3-Blöcke werden assembliert; eine bloße Vertex-Diagonale wäre nicht dieselbe Triangle-Diskretisierung. Der Solver akkumuliert den negativen Kraftvektor als Residuum.

**Quasi-Newton:** Normalen/Flächen werden an jedem Iterate neu berechnet. Die positive Geschwindigkeitsableitung hält die Geometrie fest; Follower-Load- und räumliche Fluidgradienten sind keine vollständigen Bestandteile dieses Tangentenoperators. Die Tests belegen untersuchte Grenzfälle, keine globale Konvergenzgarantie für beliebige gefaltete Kleidung.

Die reduzierte Python-Zeitintegrationsreferenz löst zusätzlich einen eindimensionalen Impuls-/Feder-/unilateralen Kontaktfall. Sie dient analytischen Grenzprüfungen und ersetzt weder den nativen Shell- noch den nativen Kontakt-Solver.

## Vorhandene FLIP-Daten und Benetzung

Untersucht wurden die unveränderten Dateien unter:

`F:/16_Cloth_neXt/SW04/SW04_NEON_flip_fluid_cache/bakefiles`

- Partikelpositionen und -geschwindigkeiten: vorhanden; die vorherige Rekonstruktionsprüfung ergab identische native Geschwindigkeits-/Occupancy-Caches.
- Vollständige Oberflächen: `000001.bobj`, `000015.bobj`, `000030.bobj` ausgewertet. Alle drei sind geschlossen, konsistent orientiert und besitzen positives Volumen. Preview-Meshes wurden nicht für die Benetzung verwendet.
- Bounds/Metadaten: vorhanden; Wasserauflösung 125×250×125, dx≈0,0160284 m. Das Kopplungsgrid 64×128×64 ist gröber. Geschwindigkeit kommt aus diesem vorhandenen Kopplungsgrid.
- Die FLIP-Importerquelle transformiert `(BOBJ-baked_bounds_min)*scale + current_domain_min`. Im untersuchten Fall sind beide Minima identisch und scale=1: Die verwendeten BOBJ-Koordinaten liegen bereits im passenden World-Meter-Raum. Eine erneute Translation mit dem Partikel-Cache-Matrixoffset wäre falsch.
- Innen/Außen der geschlossenen Oberfläche erlaubt eine **geometrische Benetzungsabschätzung** an drei symmetrischen baryzentrischen Punkten je Triangle. Das ist weder eine exakte Wet-Area-Integration noch eine Druckmessung. Die unterschiedliche Centroid- und Dreipunkt-Schätzung ist mitprotokolliert.
- Kein exportiertes Druckfeld und keine benannten SDF-/Level-Set-Dateien im untersuchten Bake-Verzeichnis gefunden. Die installierte FLIP-Bakequelle schreibt `.fpd` als `get_fluid_particle_debug_data()`. Druck-Solver-Statistiken enthalten Iterations-/Fehlerinformation, keine räumliche Druckverteilung.
- Die zeitliche Oberflächen-/Geschwindigkeitsauflösung beträgt hier 30 Frames/s. Unterhalb der Zell-/Frameauflösung liegende Spritzer oder kurze Einschläge sind damit nicht vollständig aufgelöst.

Die Offline-Auswertung benutzt Occupancy nur für den getrennt ausgewiesenen Vergleich `particle_support_weighted_area_m2`. Die neue Kraft wird durch Oberflächen-Innen/Außen bestimmt, nicht durch Occupancy-Masse. Benetzte Punkte ohne gültige Geschwindigkeit werden ausgewiesen und nicht mit erfundener Geschwindigkeit belastet; bei den neun ausgewerteten Fällen war diese Fläche null.

Hydrostatischer Druck und Auftrieb bleiben aus. Dafür fehlen verlässliche Druckrandbedingungen, Seitenbelegung/Druckdifferenz und ein Textilmodell für Durchlässigkeit/Verdrängungsvolumen. Ein offenes dünnes Gewebe darf nicht pauschal als geschlossener schwimmender Körper behandelt werden.

## Implementierte Dateien

Cloth NeXt:

- `cloth_next/gaia/hydrodynamics.py`: SI-Triangle-Last, vollständige Geschwindigkeitsblöcke, Restflächen-Massen und reduzierte implizite Referenz.
- `cloth_next/ppf/hydrodynamic_capability.py`: explizit verweigerte Freigabe bis zum geprüften nativen Transport.
- `cloth_next/blender/water_flow.py`: deaktivierte Anzeige „Hydrodynamic Pressure“. Keine neue wirksame Default-Einstellung und kein erfundener PARAM-Schlüssel.
- `tools/validate_hydrodynamic_native.py`: offizielle Scene-Exportwerkzeuge, direkter Start des explizit angegebenen experimentellen Executables, analytische Vergleiche und echte native Kontakte.
- `tools/audit_neon_hydrodynamic_loads.py`: read-only BOBJ-/Flächen-/Kraftanalyse auf vorhandenen Trajektorien.

Separater Upstream-Checkout:

`C:/Users/timch/Documents/GitHub/ppf-contact-solver-hydrodynamics`

Basis: `7232f4033515e1e72359f4ce5145456ce7d156f7` im [offiziellen Projekt](https://github.com/st-tech/ppf-contact-solver).

- `face_force.kernel.cpp`: backendneutrales Triangle-Kraftgesetz und volle CSR-Streuung.
- `driver/assemble.rs`: eigener hydrodynamischer Assembly-Pass plus native Kraft-/Matrixprüfungen; alte elastische Passes erhalten Null-/Aus-Argumente.
- `driver/step.rs`: neuer Pass nach bestehender Kontakt-/Strain-Stiffness-Kalibrierung und vor dem eigentlichen Newton-Lösen. Hydrodynamische Dämpfung vergrößert nicht die elastische Referenzmatrix zur Kontaktsteifigkeitswahl.
- `backend.rs`: experimentelle SI-Weltmaßstabprüfung, Kennzeichnung des Testmodus.
- `main.rs`: Probe unterscheidet `uniform-sheet-si-v1` und ausdrücklich `hydrodynamic-fluid-transport: unavailable`.

Der experimentelle Prozess liest nur bei gesetztem `PPF_EXPERIMENTAL_HYDRO_TEST_JSON` die vorgegebenen Testgrößen. Ohne Variable bleibt der neue physikalische Pass aus. Der Eingang akzeptiert keine unbekannten Felder und verweigert negative/nichtendliche Größen, ungültige Benetzung und Nicht-SI-Weltmaßstab.

Quellpatch zum Übertragen: `validation/gaia026/hydrodynamics/upstream/hydrodynamic-uniform-native.patch`. Keine Binary im Addon oder im Patch. Ursprünglicher Upstream-Checkout und installierte Solver-Binaries wurden nicht ersetzt.

## Tests und gemessene Ergebnisse

Belege: `validation/gaia026/hydrodynamics/`.

- Python: 19 neue Referenz-/Capability-Tests; mit bestehenden Water-Blender-Tests 32 bestanden.
- Breitere fokussierte Addon-Regressionen (GAIA, Water, Bridge, Hydrodynamik): **175 bestanden**.
- Separater nativer CPU-Checkout: **578 bestanden, 9 ignoriert, 0 fehlgeschlagen**. Darunter drei neue Tests der tatsächlich generierten nativen Kraft-/Matrixassemblierung.
- Vollständige native CPU-Zeitintegration: elf synthetische Szenen, jeweils 0,1 s; existierende Kontakte aktiviert. Direkter Prozessaufruf, kein Host-Postprocessing der Bewegung.
- Nativer CUDA-Build versucht. Er scheitert beim Linken des Upstream-Buildskripts an fehlender `libsimbackend_cuda.lib`, bevor die hydrodynamischen CUDA-Kernels ausgeführt werden können. **Keine erfolgreiche CUDA-Validierung behauptet.**

Freie Fläche, Wasser 0,2 m/s, Stoff 1 kg/m², rho=1000 kg/m³: Analytische mittlere Geschwindigkeit im letzten Ausgabeintervall 0,17937367 m/s. Nativ dt=0,001 s: 0,17678775 m/s; dt=0,0005 s: 0,17849058 m/s. Fehler sinkt von 0,002586 auf 0,000883 m/s. Die freie analytische Lösung lautet v(t)=U−U/(1+rho*Cn*U*t/(2*areal_density)). Es wird keine Sollverschiebung erfunden.

Grobe Fläche mit zwei Triangles: 0,17678775 m/s; verfeinerte Fläche mit acht Triangles: 0,17678720 m/s. Differenz ca. 5,5e-7 m/s. Verdoppelte Flächendichte: 0,16199413 m/s; analytisch 0,16262162 m/s. Gegenströmung liefert symmetrisch negative Geschwindigkeit. Dry und parallele Strömung bleiben bis auf Rundung ohne Normalbewegung.

Gepinnter Biegetest: mittlerer Hinge-Winkel 0,203922 rad bei Bend=0,01, gegenüber 0,065940 rad bei Bend=100. Die maximale Verschiebung allein ist hier kein monotoner Biegesteifigkeitsindikator, weil das steifere Blatt stärker als Ganzes um die gepinnte Kante rotieren kann.

Statischer Collider bei x=0,006 m: maximale Cloth-x-Position 0,00512408 m, native Kontaktzahl bis 11. Animierter Collider endet bei x=0,004 m: Cloth maximal x=0,00304930 m, Kontaktzahl bis 10; Cloth folgt mit etwa −0,01993 m/s dem entgegenkommenden Collider. Die verbleibenden Abstände entstehen durch die vorhandenen Kontaktparameter und Barriermechanik, nicht durch einen neuen hydrodynamischen Abstandszwang. Keine Collider deaktiviert oder umgangen. Ein direkter nativer Kraftvektor der Kontaktreaktion wurde nicht exportiert.

### Abdeckung der angeforderten 15 Fälle

| Fall | Nachweis |
|---|---|
| 1 keine Flüssigkeit | Python, native Assembly, vollständiger nativer Dry-Lauf |
| 2 ruhendes Wasser/Stoff | Python und native Assembly |
| 3 senkrechte Anströmung | analytische Kraft, native volle Matrix und Zeitintegration |
| 4 parallele Strömung | Null-Normalkraft, separater Ct-Test, nativer Lauf |
| 5 Gegenseite | native Kraft und symmetrische negative Bewegung |
| 6 doppelte Geschwindigkeit | vierfache Kraft, Python und native Assembly |
| 7 doppelte Fläche | doppelte Newton-Kraft, Python und native Assembly |
| 8 Flächendichte | Restmassenreferenz und zwei native freie Läufe |
| 9 Biegesteifigkeit | zwei native gepinnte Shell-Szenen, Hinge-Winkel |
| 10 gegen Collider | nativer statischer Kontaktlauf mit Statistik und Abstandskontrolle |
| 11 schnell/kleine Belegung | native Kraft bei 20 m/s und 0,001 vorgeschriebener Wet-Area; reduzierte Integration bei 50 m/s. Keine Ableitung aus Partikel-Occupancy |
| 12 Mesh-Auflösung | native 2-/8-Triangle-Vergleich und analytische Kraftsumme |
| 13 Zeitschritt | native dt-/dt/2- und reduzierte analytische Konvergenz |
| 14 animierter Collider | nativer Lauf mit bewegter gepinnter Collision-Mesh-Fläche |
| 15 Newton/Kontakt/endliche Werte | native Prozessabschlüsse, lineare Residuen, Kontaktstatistiken, endliche Arrays; Python-Finite-Differenzen-Jacobian |

Die protokollierten `reresid` sind lineare relative Solverresiduen, kein unabhängig gemessener vollständiger nichtlinearer Follower-Load-Residual. Ein universeller Newton-Konvergenznachweis wäre eine Überbehauptung. Reibung bei verschiedenen Auftreffwinkeln, komplexe Kleidung/Self-Contact unter neuer Wasserlast und hochaufgelöste Spritzer brauchen weitere native Integrationsszenen.

## NEON: Vergleich der vorhandenen Trajektorien mit geschätzter Wasserlast

Die ursprünglichen Dry-/Native-Flow-Simulationen und Material-/Kontaktbefunde stehen unverändert im vorherigen Untersuchungsbericht. Für das Hauptkleidungsstück war das ursprüngliche Wet-/Dry-Signal etwa 0,126–0,140 mm RMS; das belegt die schwache Wirkung dieses konkreten alten Kraftgesetzes, keine Größenordnung für das neue Modell.

Für die neue Kraftschätzung wird die vorhandene `zone30-bound/dry1`-Trajektorie benutzt. Aktuelle deformierte Triangles, aktuelle Cloth-Geschwindigkeit und native rekonstruierte Fluidgeschwindigkeit werden gemeinsam ausgewertet. Es gibt dabei keine Rückwirkung der geschätzten Last auf die Trajektorie.

| Frame | Kleidungsstück | benetzte Fläche m² | Summe lokaler Kraftbeträge N |
|---:|---|---:|---:|
| 1 | Hauptkleidungsstück | 0,552792 | 0,139418 |
| 15 | Hauptkleidungsstück | 0,557495 | 7,361098 |
| 30 | Hauptkleidungsstück | 0,602227 | 16,703981 |
| 1 | .001 | 0 | 0 |
| 15 | .001 | 0 | 0 |
| 30 | .001 | 0 | 0 |
| 1 | .002 | 0,008667 | 0,001527 |
| 15 | .002 | 0,009880 | 0,025218 |
| 30 | .002 | 0,040674 | 0,441075 |

Die Summe der Kraftbeträge ist nicht der Betrag der resultierenden Gesamtkraft. Am Hauptkleidungsstück an Frame 30 ist der Netto-Vektor in Blender-World-Metern etwa (−10,413; 5,925; 0,403) N. Gegenläufige Lasten können sich teilweise aufheben und dennoch lokal verformen.

Die steigende Last enthält auch die Relativbewegung des fallenden trockenen Stoffs durch Wasser. Sie darf nicht als ausschließlich von einer Welle verursachte Last oder als gemessene neue Bewegung interpretiert werden. Mit der neuen Kraft würde die Cloth-Geschwindigkeit anders verlaufen; deshalb wäre ein Addieren dieser Kräfte auf die vorhandene Trajektorie kein valider neuer Solve.

Die Dreipunkt-/Centroid-Wet-Area-Schätzungen unterscheiden sich am Hauptkleidungsstück an Frame 30 um ca. 0,000338 m². Das ist ein lokaler Quadraturvergleich, keine vollständige Fehlergrenze der rekonstruierten FLIP-Oberfläche oder des Geschwindigkeitsgrids.

## Konkrete noch erforderliche Upstream-Arbeit

1. Separater hydrodynamischer Feldtyp mit expliziten SI-Einheiten, Dichte, normalem/tangentialem physikalischen Closure-Modell und Target-Zuordnung. Kein Umdeuten des bisherigen Occupancy-Kanals.
2. Native Abfrage von Geschwindigkeit und einer unabhängig belegten Flüssigkeitsgrenze an Triangle-Quadraturpunkten. Vorhandene sparse FluidVolume-Residency/Interpolation kann wiederverwendet werden; Benetzung benötigt einen getrennten SDF-/Oberflächenkanal mit Framezeiten und überprüften Transformationen.
3. Versionierter experimenteller Transport für Oberflächen-/SDF-Daten und deren Provenienz. Bei NEON können die vollständigen BOBJ-Oberflächen die Grundlage liefern. Ihre Verarbeitung muss Quellsignaturen, Maßstab, geschlossene Volumen, Rasterauflösung und Speicherbudget prüfen.
4. Dreipunkt-/adaptive Wet-Area-Integration im nativen Kernel und entsprechend baryzentrische Kraft-/Jacobian-Verteilung. Centroid-Integration ist für gleichförmige volle Benetzung geprüft; kleine Spritzer und schmale benetzte Bereiche brauchen diese Erweiterung.
5. Kraft-/Kontaktreaktions-Telemetrie in N, nicht nur Kontaktzahlen; vollständiger neuer NEON-Dry/Flow/Hydro-Vergleich mit unveränderten Material- und Kontaktbedingungen.
6. CUDA-Bootstrap/Build-Rezept reparieren und danach echte CUDA-Werte gegen CPU-Referenzen prüfen. Vorher keine GPU-Capability freigeben.
7. Erst nach diesen Prüfungen den fail-closed Addon-Gate mit einem verifizierten Binary-/Transport-Capability-Nachweis verbinden. Eine handgeschriebene Manifestbehauptung oder die aktuelle experimentelle Probe genügt nicht.

Einwegkopplung bleibt eine Grenze: Wasser wird nicht am Gewebe umgelenkt, erfährt keine Gegenkraft und verliert keine Energie durch das Cloth. Abschattung, Stau, Verdrängung, Durchströmung und Impulsrückwirkung sind damit nicht vollständig erfasst. Die geprüfte lokale Kraftintegration ist ein belastbarer Entwicklungsschritt, noch keine vollständig gekoppelte Wasser-/Textilsimulation.
