# Cloth NeXt 3.0 — Beta-Checkliste (Beta 16.11., Stable 28.11.2026)

Stand: 2026-11-06 morgens. Grundlage: `CODEX_CLAUDE_STATUS.md`, `docs/RELEASE_POLICY.md`, Changelogs, Branches auf GitHub, Projekt-Threads.
Nichts hiervon ist erledigt, nur aufgeschrieben. **[ENTSCHEIDUNG]** = du musst entscheiden.

## 0. Zwei Punkte, die zuerst geklärt werden müssen

- [ ] **[ENTSCHEIDUNG] Versionsnummer der Beta.** Die Release-Policy erlaubt für Beta nur `STABLE.BETA.0` mit BETA ≥ 1, Stable nur `STABLE.0.0`. Eine Beta mit der Nummer `3.0.0` ist damit nicht möglich (das wäre schon Stable). Naheliegend: **Beta = `2.10.0`**, Stable = `3.0.0`. Alternative wäre eine Policy-Änderung, die nur du freigeben kannst.
- [ ] **[ENTSCHEIDUNG] Code-Signing in der Beta oder nicht** (siehe `CODE_SIGNING_PLAN.md`). Laut Policy muss neue Release-Infrastruktur erst durch eine Beta. Wenn signiert werden soll, Zertifikat jetzt bestellen.

## 1. Code-Stand einsammeln (bis ca. 12.11.)

- [ ] `main` steht auf 2.9.63 (`218f0f7`). 2.9.64 liegt nur auf `claude/project-thread-mhu45t` (AETHER: Wind, OGC-artiges Vorschieben, saubere Starts, Grab ohne Stretch-Limit, Navigation flüssig). Nach deinem OK als PR nach `main`.
- [ ] **2.9.63 und 2.9.64 sind nicht im Dev-Kanal veröffentlicht** (zuletzt veröffentlicht: 2.9.62). Mindestens eine Dev mit allem Stand vor der Beta veröffentlichen und in deinem eigenen Blender prüfen.
- [ ] Laufende Threads, die in die Beta sollen oder bewusst nicht:
  - "Shrink und Surface Offset für AETHER bauen" (läuft gerade) — rein in 3.0 oder 3.0.x?
  - "Fitting-Durchdringungen in SW04 beheben" (wartet auf Review) — offen war der Grab-Freeze bei Vertices unter 20 µm Abstand.
  - "Neue Technologie für AETHER bewerten" — OGC ist laut deiner Entscheidung vom 04.11. erst 3.1; `AETHER_EXP_DAT` bleibt aus. Aber: der in 2.9.64 enthaltene "OGC-style pre-push" (Engine `7bbd146`) ist standardmäßig **an**. Bewusst so gewollt? Bitte bestätigen.
  - "Marktstellung von Cloth neXt sichern" und "Sewing UI Icons" warten auf dich.
- [ ] Ab Beta-Stichtag: nur noch Fixes, keine Features (Feature-Freeze schriftlich festhalten, damit parallele Threads sich daran halten).

## 2. PATTERN NeXt — Bugstatus

- [x] **Workspace reagiert nicht auf Klicks/Tasten**, wenn ein anderes Add-on einen Modal-Operator im Hintergrund laufen lässt (bei dir Blaze Puppeteers Hover-Tracker): behoben in 2.9.63, PR #85, auf `main` gemergt am 04.11., CI grün.
- [ ] **Noch nicht in deinem Blender bestätigt**, weil 2.9.63 nie veröffentlicht wurde. Nach der nächsten Dev mit deinem echten Setup (Blaze Puppeteer an) prüfen: Workspace öffnen, Polygon zeichnen, Pattern bearbeiten, Naht setzen, Undo/Redo, Speichern und neu öffnen.
- [ ] Bekannter Altfehler (nicht PATTERN, aber Sewing): GUI-Akzeptanztest "Ctrl+Z undoes the last seam and exits cleanly" ist auf `main` rot. Vor der Beta fixen oder als bekanntes Problem listen.
- [ ] Bekannte Grenze: Factory-Startup-Tests sehen keine Fremd-Add-ons. Für die Beta einen Durchgang mit deiner normalen Blender-Konfiguration einplanen.

## 3. GAIA / Wasser — offene Abnahmepunkte (Thread "Weg zu 3.0.0 prüfen", 24.10.)

- [ ] **[ENTSCHEIDUNG]** Wet Fabric für 3.0 als "experimental" kennzeichnen (Empfehlung des Abnahme-Checks).
- [ ] **[ENTSCHEIDUNG]** Pressure Strength: Default 1, im Tooltip als künstlerischer Regler.
- [ ] Gates G3 (Freistoff-Test mit Referenz) nicht gelaufen, G7 (Selbstkontakt) war FAIL. Neu bewerten oder bewusst als bekannte Grenze dokumentieren.
- [ ] Physik-Messungen mit dem ausgelieferten GAIA-Build wiederholen (GPU — nur wenn kein anderer Thread die GPU nutzt; GPU-Lock verwenden).
- [ ] Wird Linux in 3.0 offiziell unterstützt (GAIA-Linux bisher nur WSL2 getestet)? Wenn nein, in den Notes klar sagen.

## 4. Release-Infrastruktur

- [ ] `docs/RELEASE_POLICY.md` §8 sagt noch "genau eine Cloth-NeXt-Exe" (`bin/cloth-next-bake.exe`). Der Scanner erlaubt längst auch AETHER und TETHYS Engine. Text anpassen (Policy-Änderung braucht dein OK).
- [ ] `resources/onboarding/whats_new/<beta-version>.json` redaktionell schreiben (2–5 Highlights; darf nicht automatisch aus dem Changelog erzeugt werden). Danach dasselbe für `3.0.0`.
- [ ] `CHANGELOG.md`, `RELEASE_NOTES.md` und Manifest-Version auf die Beta-Version (Entwurf der Notes: `RELEASE_NOTES_DRAFT.md`).
- [ ] Beta- und Stable-Archive dürfen kein `dev_build.json` enthalten und keine Developer Tools aktivieren. Prüfen, dass Dev-only-UI (z. B. TETHYS Debug, Testwerkzeuge) im Beta-Paket aus ist.
- [ ] ZIP ≤ 100 MiB (AETHER-Exe ist ~9,6 MB, Dev-ZIPs lagen um 34 MB; mit allen Engines neu messen).
- [ ] `release-preflight` gegen den exakten Commit laufen lassen, Kandidat-Artefakt ansehen, erst dann Tag setzen (Tag erst nach deinem Go; Tags sind unveränderlich).
- [ ] Falls Signing: Signier-Schritt + Signaturprüfung in `build-release-candidate.yml` und vorher einmal in einer Dev durchlaufen lassen.

## 5. Beta-End-to-End-Test (Pflicht laut Policy vor Stable)

Auf einem sauberen Windows-Benutzerprofil mit der Beta aus dem Kanal (nicht aus dem Repo):

- [ ] Frische Installation: Welcome erscheint einmal, What's New nicht gestapelt.
- [ ] Update von 2.9.6x: What's New genau einmal; Neustart / Datei öffnen / Re-Enable wiederholen es nicht.
- [ ] Update während Tray-Companion läuft und während einer AETHER Live Simulation (darf nicht mit WinError 5 scheitern).
- [ ] PolyWorks-Konto: Anmeldung, GAIA-Berechtigung, Offline-Fall (gespeicherte Berechtigung).
- [ ] GAIA-Install aus den Preferences, ein GAIA-Bake, ein AETHER-Bake, AETHER Live mit Grab, Stop & Keep.
- [ ] GAIA Water Flow (eine Quelle) + TETHYS Bake Wetness und Bake All Wetness, Cancel und Fortsetzen.
- [ ] Cloud Bake (falls im 3.0-Umfang): Quote, Senden, Download, Import.
- [ ] PATTERN NeXt-Durchgang aus Abschnitt 2, mit deinen normalen Add-ons.
- [ ] F12-Animationsrender einer Szene mit Wind (EEVEE-Absturz vom 03.11. ist sehr wahrscheinlich ein Blender-Bug; als bekanntes Problem notieren, falls er wieder auftritt).
- [ ] Deaktivieren/Deinstallieren: kein Prozess (Companion, Engines) bleibt übrig.
- [ ] Unterstützte Blender-Versionen festlegen und auf jeder mindestens den Smoke-Test.

## 6. Vertrieb und Kommunikation

- [ ] Superhive-3.0-Seite: Entwurf liegt auf gh-pages unter `/superhive-3/` (live-iframe unverändert). Texte mit den finalen Notes abgleichen; erst zum Stable live schalten.
- [ ] Bekannte Probleme für Beta-Tester sammeln (siehe oben: Sewing Ctrl+Z, G7, Wind-Hindernisse in AETHER, Linux-Status).
- [ ] Wo melden Beta-Tester Fehler? (Kanal, Vorlage mit Log-Pfad `companion-crash.log`, Blender-Version, GPU.)
- [ ] GAIA-Server-Build: `--audience public` erst mit deinem ausdrücklichen Go; die falsche "rods"-Zeile in den Server-Notes korrigieren (nur du in der Administration).

## 7. Zeitplan-Vorschlag

| Datum | Meilenstein |
|---|---|
| 06.–07.11. | Entscheidungen aus Abschnitt 0 und 3; Zertifikat bestellen |
| bis 12.11. | Laufende Threads gemergt oder zurückgestellt; letzte Dev veröffentlicht und von dir geprüft |
| 13.–14.11. | What's New, Notes, Policy-Text; Preflight; ggf. Signing in einer Dev |
| 15.11. | Preflight auf finalem Commit grün, dein Go |
| 16.11. | Beta-Tag und Veröffentlichung, danach E2E-Test aus Abschnitt 5 |
| 16.–25.11. | Nur Fixes; Beta-Feedback |
| 26.11. | Preflight Stable, Superhive-Seite final |
| 28.11. | 3.0.0 Stable |
