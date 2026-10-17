# Abschlussstand � 17.10.2026

Read docs/GAIA_RECONSTRUCTION_ACCEPTANCE_RESULT.md first. Full regular CUDA
Blender130�159+real160 PASS, fresh Blender all30PC2frames/all3cloths PASS,
all30independent collider frames0crossings, native/PC2 max6.104e-8m, reload0,
output SDF clearance0.22102m, Cn1/Ct0/materials/186attachments unchanged.
All4long native sensitivity replays complete; load/wetting/temporal-difference
screens PASS; all peak at152. Native repeatability is NOT resolved:unchanged
runs max26.13mm vs operator /22.59mm between repeats. Prospective8mm trajectory
maximum remains INVESTIGATE; do not blame this on reconstruction or raise limits.
ReleaseNO-GO; historical CPU/CUDA self-contact19/20 remains. Physical response,
visual coherence, numerical finiteness and repeatability are separate criteria.
�2.5mm is stored signed-EDT scalar offset, NOT certified geometric displacement.
All31reconstructed source meshes closed with same component/triangle counts;
157retains original4zero-area faces;149/151one fewer duplicate vertex each.
93FLIP source payload hashes unchanged; frozen baseline blend hashd0a7055f�
unchanged. Current original blend hashc22a2da5� has last write16Oct15:31UTC,
predating current tests. Do not overwrite/revert it or claim test of newer scene.
32targeted Python regressions PASS. Diagnostic30frontframes+3side views and
10fps inspection GIF saved. No solver binary/physics changes, version/commit/
push/tag/release. All experimental work/logs retained. No active test pending.

The chronology below is retained and superseded where marked.

# Live run � 17.10.2026

Short regular CUDA Bake130�132 + real133 PASSED (`test-d-reconstructed-130-132`),
fresh Blender PC2 reload PASS, all3collider audit frames0crossings, native SDF
output clearance0.2259735m. Hash-bound experimental surfaces130�160 frozen;
149/151 source replay and independent occupancy controls PASS (no changed
triangles/components; one fewer duplicate vertex each). Python regressions32PASS.
FULL regular Blender130�159+real160 is ACTIVE: launcher exec session62807,
resource monitor session86213. Label `test-e-130-159`, root
`.hydro-test-tmp/blender-e2e-20261016`. NEVER restart or interrupt an active test.
Last observed water preparation passed, regular scene EXPORTING around197s.
After completion: fresh Blender verifier, independent all30collider audit,
report_neon_regular_blender_bake, native temporal diagnostics and diagnostic
native geometry views (`blender_render_neon_native_validation.py`), visual review.
Full sample1�30 test remains separate. No release/commit/version/solver change.

# Current checkpoint � 17.10.2026

GAIA resumed. Read docs/GAIA_ORIGINAL_MESHER_RECOVERY.md and
GAIA_PRACTICAL_RECONSTRUCTION_ACCEPTANCE.md first. User revised geometric
acceptance explicitly: no automatic NO-GO from ~2.5mm nearest-vertex distance.
Original chunk proximity weld bug proved; canonical grid-edge candidate closes all
130�160. Valid131�134 transport bit-identical; five actual native sensitivity solves
completed, fine SDF wetting passes9/9. Do not modify original source data/binaries.
Next: finish independent controls149/151 (one duplicate vertex difference), freeze
hash-bound reconstructed sources for ISOLATED test only, short real Bake130 then
full130�159+160, PC2 reload/collider/temporal/visual review. No release.
The historical checkpoint below is retained and partly superseded.

# Arbeitsstand – Quellenprüfung abgeschlossen: Rekonstruktion NO-GO, 16.10.2026

Nutzer hat GAIA nach Abschluss der Account-Optik wieder zur Fortsetzung freigegeben.
Aktuell: separater echter Blender-Test `test-c-1-30-r2` BESTANDEN: Operator
FINISHED nach 352,43 s, frischer Blender-Reload mit allen 30 PC2-Samples PASS,
30 unabhängige Collider-Frames ohne Schnitte, SDF-Mindestabstand 0,234959 m.
Belege: `blender-1-30/summary.json`, `verification.json`, `collider-audit/`.
Native/PC2 maximal 6,104e-8 m; ausgewertetes Reload-Playback exakt.
Vergleich mit alten nativen Hydro1/2: gleiche Anfangspositionen, Hauptstoff
1,48 mm RMS / 20,45 mm max. Keine Gleichwertigkeit behaupten: native Referenz
hat min-newton4/cg-tol1e-4/max-dx0.001; regulärer Original-Bake 1/0.01/1.0.

Nächste Arbeit: originaler FLIP-Mesher. Cache-Log .009 benennt Version1.8.8
Individual Release2026-07-27 /759LY; installiertes Testprofil ist ebenfalls1.8.8.
SQLite enthält statischen Cube, statischen Körpercollider und statische Kurve;
keine animierten/keyframed Meshreihen. Originalgeometrie ist verfügbar!
Installierter Engine-API-Probetest (512 synthetische Partikel): update(0) führt
effektiv1e-6 s aus und verschiebt Partikel maximal1,013e-6 m. NICHT auf NEON
anwenden. Es gibt unter738 DLL-Exports keinen SDF-Export/standalone Mesher.
Belege: `flip-original/probe-r2/probe.json`. Erster Probeversuch scheiterte
nur am Harness-Attributnamen deltaTime statt delta_time; Rohlog erhalten.
Offizieller öffentlicher v1.8.8-Quellstand wird separat heruntergeladen; Demo-
Edition nicht als byte-identisch zur Individual-Engine annehmen. Ziel ist ein
separater Mesher-Aufruf ohne FluidSimulation.update, zunächst nur Referenztests.

Der vorige Harness-Fehler ist belegt: ein relativer
Recovery-Projektpfad wurde aus dem Runtime-CWD aufgelöst und verdoppelte den Pfad.
Nur die beiden Testharness-Einstiegspunkte geändert; keine Solverphysik geändert.
Ressourcenmessung `resource-samples.json` läuft separat (5-s-Stichproben).
Die folgende Zurückstellung beschreibt den vorigen Stand:
Der bereits gestartete separate echte Blender-Test 1–30 mit Lookahead31 wurde
nicht unterbrochen. Er endete nach 300,16 s mit ERROR im Solver-Projektaufbau
(`official_scene_worker.apply_bindings`), vor einem erfolgreichen nativen Lauf.
Alle Materialien / 186 Attachments im Export erhalten. Ergebnis NICHT bestanden;
kein Ersatz für 130–159. Unveränderte Rohdaten und Fehler unter
`.hydro-test-tmp/blender-e2e-20261016/test-c-1-30/`, dazu `.log` und Launch-Log.
Keine Fehlerbehebung oder erneuter GAIA-Lauf nach Zurückstellung gestartet.

Vor Beginn der PolyWorks-Clientarbeit alle neuen Quellenprüfungen beendet und
gesichert: sämtliche 250 BOBJ-Frames topologisch geprüft, 130–160 vollständig
mit Problembereichen/Domain-Abstand und Partikel-/Velocity-Payloads geprüft.
Sechs analytische Partikelkandidaten geprüft; keine Rekonstruktion freigegeben.
Bericht: `docs/GAIA_SURFACE_RECONSTRUCTION_INVESTIGATION.md`.
Keine neuen Blender-Bakes gestartet: Kraft-/Benetzungsäquivalenz unbewiesen.
Explizites NO-GO statt Lochfüllung, Radiusanpassung oder gelockerter Validierung.
Alle vorigen Solverarbeiten, Binaries und Ergebnisse unverändert erhalten.

## Voriger erfolgreicher Blender-Test

Aktueller Stand nach neuem Nutzerauftrag: regulärer CUDA-Bake über
`bpy.ops.clothnext.bake`, Test A 131–133 mit echtem Fluid-Lookahead 134 bestanden.
Alle drei Cloths und 186 Verbindungen erhalten. Native/PC2-Abweichung maximal
6,102e-8 m; nach Reload exakte PC2-Playback-Geometrie. Dry-Kontrollbake ebenfalls
bestanden; Hauptstoff Hydro vs. Dry maximal 56,248 mm. GUI-Re-Bake gestartet,
während SDF-Vorbereitung mit echtem Cancel-Button abgebrochen, bestehende drei
PC2-Dateien hashgleich erhalten. Sichtbares Timeline-Playback geprüft und pausiert.
Null eigene native Solver-/Control-Server-Prozesse verbleiben. Die separate
Test-GUI bleibt offen; die ursprüngliche Blender-Instanz wurde nicht verändert.

Test B 130–159 ist NICHT bestanden: Quellframe 130 hat sieben offene und eine
nicht-manifold Kante; auch 135 ist betroffen. Getrennte geschlossene Teile sind
erlaubt (Frame 131: 378 Komponenten). Keine Löcher automatisch füllen, keine
Frames überspringen und fehlende SDF nicht als trocken behandeln.

Neuer Hauptbericht: `docs/GAIA_HYDRODYNAMIC_BLENDER_E2E.md`.
Belege: `blender-e2e/summary.json`, `hydro-verification.json`, `dry-verification.json`,
`native-bounds.json`, `collider-audit/collider-crossing-audit.json` und 357 bestandene
Tests in `blender-e2e/addon-tests.log`. Historische Operator-Harness-Berichte r9
und Dry enthalten noch Harness-FAIL, obwohl der Operator FINISHED ist; die
separate abschließende Verifikation ist PASS. Nichts davon nachträglich überschreiben.

Testdateien liegen unter `.hydro-test-tmp/blender-e2e-20261016/` im Add-on-Repo:
`test-a-131-133-r9/NEON-test.blend`, `dry-131-133/NEON-test.blend`, eigenes Runtime-
Bundle und eigenes Profil. Native Dateien sind in den jeweiligen Cache-Recovery-
Projekten erhalten. Native SDF-Guard im separaten Solver-Checkout ergänzt und
CUDA neu gebaut; Worker-SHA256 bb10a7e97ced6f4a7b42637ad5822dd1546a70a3206ec0504a1ef6ef2e728a94,
DLL d325f4d65b94717f2807263cd9a3ea9e455d2cbab5b1be38258708f2606c6005.
Voriger DLL-Stand unter `.tools/hydro-frozen-spatial-r1` im separaten Solver-Repo
gesichert; eingefrorene r1/r3-Worker nicht überschrieben.

357 relevante Add-on-Tests bestanden; originale Szene/FLIP/GAIA/official Binaries
unverändert, keine Versionsänderung und keine Git-Veröffentlichung. Bekannte
Selbstkontakt-/Wiederholungsstreuung bleibt offen. Klassische GUI geprüft;
Floating-HUD, langer GUI-ETA-Test, GUI-Löschdialog und Resume nicht freigegeben.

## Voriger nativer CPU-/CUDA-Abschluss

Fortsetzung am 16.10.2026 ausdrücklich angefordert und abgeschlossen: Alle fünf
CPU-Fälle sind aus Frame-19-Checkpoints in separaten Session-Kopien unter
`ppf-contact-solver-hydrodynamics/.tools/neon-cpu-resume-20261016-r1`
vollständig abgeschlossen worden.
Runner: `tools/resume_neon_hydrodynamics.py`, Protokoll
`neon-spatial-cpu-resumed-r1.log`, Ergebnisse in `neon-spatial-cpu-resumed-r1`.
Die unten beschriebenen ursprünglichen Teilausgaben bleiben unangetastet.
Die neue Zeitmessung erfasst nur den fortgesetzten Abschnitt und ist kein
vollständiger CPU-Laufzeitvergleich mit den ununterbrochenen GPU-Läufen.
Alle zehn CPU-/CUDA-NEON-Fälle haben vollständige endliche 30-Frame-Trajektorien.
Der Backend-Vergleich ist in `neon-cpu-cuda-comparison.json` ausgewertet; die
unabhängige Schnittprüfung findet in 300 Ausgabeframes null Kreuzungen.
Zwei direkte GPU-Kraft-/Matrixtests bestehen, auch bei eingefrorener Kontakt-
Geometrie. Der ursprüngliche strenge zustandsabhängige Kraftvergleich bleibt
bei 19/20 bestandenen Fällen. Nächste Arbeit ist dessen Akzeptanz-/Kontakt-
Bewertung, lokale Reproduzierbarkeit, native SDF-Ausschnittabsicherung und erst
anschließend eine produktive Blender-Anbindung. Keine automatische Fortsetzung.

Historischer Stopp vor der Fortsetzung: Alle fünf laufenden eigenen CPU-NEON-Runner und ihre fünf nativen Solver wurden
auf ausdrücklichen Nutzerwunsch beendet. Danach wurden null eigene verbleibende
NEON-Prozesse gefunden. Kein automatischer Neustart ist eingerichtet.
Die unvollständigen CPU-Ausgaben sind erhalten; sie gelten nicht als bestandene
30-Frame-Läufe. Prozesszuordnung: `user-stopped-processes.json`.

Fertig sind der isolierte CUDA-12.8-Build, tatsächliche RTX-4070-SUPER-Probe,
direkter GPU-Kraft-/Neun-Matrixblöcke-Test, 20 kleine GPU-Simulationen und fünf
vollständige GPU-NEON-Läufe über je 30 Frames. Der strenge kleine CPU-/CUDA-
Vergleich steht auf FAIL: 19/20 Kraftvergleiche bestanden, alle 20 Positions-
vergleiche innerhalb 10 µm. Selbstkontakt bleibt auch nach Wiederholung bei
etwa 1 % Kraftabweichung. Hydro-NEON-Wiederholung: Hauptstoff 0,222 mm RMS,
lokal maximal 7,691 mm. Keine Produktfreigabe, Addon-Option bleibt deaktiviert.

Hauptbericht: `docs/GAIA_HYDRODYNAMIC_CUDA_VALIDATION.md` im Repository.
Maschinenlesbar: `cuda-validation-summary.json`, `spatial-cpu-cuda-comparison.json`.
Native Quellen: separater Checkout `C:/Users/timch/Documents/GitHub/ppf-contact-solver-hydrodynamics`.
Eingefrorene CPU-EXE: `target/hydrodynamics-cpu/release/ppf-contact-solver-spatial-r3.exe`.
Eingefrorene CUDA-EXE: `target/hydrodynamics-cuda/release/ppf-contact-solver-spatial-r1.exe`.
CUDA-Laufzeit benötigt pro Prozess die lokale Backend-DLL aus
`crates/ppf-cts-compute/cuda/build/lib` und `.tools/cuda-12.8/bin` auf PATH.
Offizielle Binaries, Originalszene und Quellcaches wurden nicht verändert.

Historische Hinweise vor der inzwischen abgeschlossenen Fortsetzung: CPU-Läufe kontrolliert fortsetzen oder in neuen
Ausgabeordnern neu starten; keine alten Ergebnisse überschreiben. Vor einer
Fortsetzung prüfen, ob der native Solver brauchbare Resume-Checkpoints bietet.
Nicht blind alte Runner mit denselben Ausgabeordnern erneut starten. Nur die
vier separaten Einzel-Runner plus Hydro1 sind nötig; der frühere Hydro1-Runner
enthielt zusätzlich vier inzwischen redundante Folgefälle.
Danach vollständigen CPU-/CUDA-NEON-Vergleich ausführen und Selbstkontakt-
Parität/Wiederholungsstreuung untersuchen. Dazu ist
`tools/compare_neon_hydrodynamic_backends.py` vorbereitet.
