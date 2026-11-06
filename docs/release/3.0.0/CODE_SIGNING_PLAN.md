# Code-Signing für Cloth NeXt 3.0.0 — Plan

Stand: 2026-11-06. Nichts davon ist umgesetzt; es wurde kein Zertifikat gekauft und nichts signiert.

## 1. Welche Programme signiert werden müssen

| Programm | Herkunft | Wo es landet | Hash-Pinning |
|---|---|---|---|
| `Cloth NeXt Bake.exe` (Companion: Bake-Fenster, Tray, Konto, Welcome/What's New) | PyInstaller `--onefile`, gebaut in CI (`companion/build_companion.py`) in `publish-dev.yml` und `build-release-candidate.yml` | `bin/cloth-next-bake.exe` im ZIP, Tray-Kopie in `%LOCALAPPDATA%/ClothNeXt/companion` | `companion_manifest.json` (von `tools/stage_companion.py` erzeugt) |
| `AETHER Engine.exe` | Repo `polyworks-art/AETHER`, lokal mit CUDA gebaut und nach `vendor/aether/windows-x64/` kopiert | `bin/AETHER Engine.exe`, laufend aus einer Kopie außerhalb des Add-on-Ordners | `vendor/aether/ENGINE.json` (`sha256`, `file_size`), CI prüft gestaged = ENGINE.json |
| `TETHYS Engine.exe` | Repo `polyworks-art/THETYS`, nach `vendor/tethys/windows-x64/` kopiert (`tools/update_tethys_vendor.py`) | `bin/TETHYS Engine.exe` | `vendor/tethys/ENGINE.json`; `cloth_next/tethys/engine.py` prüft Größe + SHA-256 zur Laufzeit |

"Companion" und "Bake.exe" sind dasselbe Programm. Linux (`tethys-engine`, Linux-Companion) braucht kein Authenticode.

Optional, nicht für 3.0 nötig: die GAIA-Solver-Exes (`ppf-cts-server.exe`, `ppf-contact-solver.exe`), die vom Server geladen werden. Sie sind dein eigener Build und könnten mit demselben Zertifikat signiert werden; das ändert ihre SHA-256 und damit den Server-Build (neue GAIA-Version nötig).

## 2. Warum es sich lohnt (auch wenn Blender die Exes installiert)

- Blender lädt die Extension per Python herunter, die entpackten Exes haben deshalb meist kein "Mark of the Web". Der klassische SmartScreen-Dialog ist also selten das Problem.
- Das echte Risiko sind **Virenscanner** (PyInstaller-onefile-Exes werden häufig falsch erkannt), **Smart App Control** unter Windows 11 (blockiert unsignierte, unbekannte Programme unabhängig vom Download-Weg) und Firmen-Richtlinien (AppLocker/WDAC). Eine gültige Signatur mit Zeitstempel senkt alle drei deutlich.
- Der Herausgeber erscheint in Task-Manager, Firewall-Dialogen und Datei-Eigenschaften als "Tim Christmann" bzw. die Firma statt "Unbekannt".

## 3. Was du kaufen bzw. einrichten musst

Seit Juni 2023 müssen private Schlüssel für Code-Signing auf Hardware (USB-Token) oder in einem Cloud-HSM liegen. Eine `.pfx`-Datei als GitHub-Secret ist nicht mehr möglich. EV bringt seit 2024 keinen sofortigen SmartScreen-Vorteil mehr; **OV bzw. IV reicht**.

Seit März 2026 sind Code-Signing-Zertifikate höchstens 460 Tage gültig (etwa 15 Monate). Rechne also mit jährlicher Erneuerung.

### Option A — Azure Artifact Signing (früher Trusted Signing)
- Günstig (Basic-Tarif ca. 10 USD/Monat) und gut in GitHub Actions integriert (OIDC, keine Passwörter im Repo).
- **Für dich als Einzelperson in Deutschland nicht verfügbar:** Individual-Entwickler nur in USA/Kanada. In der EU nur für **Organisationen** (laut Microsoft-Doku u. a. mit mehrjähriger nachweisbarer Geschäftshistorie).
- Nur sinnvoll, wenn PolyWorks als eingetragene Firma existiert und die Validierung besteht. Das kann Wochen dauern und schafft den 16.11. wahrscheinlich nicht.

### Option B — IV/OV-Zertifikat mit Cloud-Signing (Empfehlung)
- Beispiel **SSL.com IV (Individual Validation) + eSigner**: etwa 129 USD/Jahr für das Zertifikat; dazu kommt eine eSigner-Gebühr für automatisches Signieren in CI (je nach Signaturen-Kontingent, Preis vor dem Kauf prüfen). Keine Firma nötig; Identitätsprüfung per Ausweis/Video.
- Alternativen: Certum (Open-Source- oder Standard-Zertifikat, SimplySign-Cloud), Sectigo-Reseller (eher mit USB-Token, ca. 200–300 USD/Jahr). Ein USB-Token funktioniert nur auf deinem PC, nicht in GitHub Actions; das wäre nur ein Notbehelf.
- Validierung: in der Regel 1–5 Werktage, mit Rückfragen länger. **Für die Beta am 16.11. müsste heute oder morgen bestellt werden.**

**Empfehlung:** Option B als Einzelperson (IV), mit Cloud-Signing, das aus GitHub Actions heraus funktioniert. Wenn du später eine Firma validieren lässt, kann man auf Azure umsteigen; der Ablauf unten bleibt gleich.

Was ich dafür von dir brauche:
1. Entscheidung Option/Anbieter und den Kauf (den Kauf machst du selbst).
2. Name, der als Herausgeber erscheinen soll (Person oder Firma).
3. Die CI-Zugangsdaten des Anbieters als GitHub-Secrets (z. B. `CODESIGN_USERNAME`, `CODESIGN_PASSWORD`, `CODESIGN_CREDENTIAL_ID`, `CODESIGN_TOTP_SECRET`). Ich setze keine Secrets und sehe sie nie.

## 4. Wo das Signieren in den Ablauf gehört

Grundregel: **erst bauen, dann signieren, dann prüfen, erst danach hashen.** Jede Signatur verändert die Datei und damit ihre SHA-256.

### Companion (CI)
In `publish-dev.yml` und `build-release-candidate.yml` (Windows-Job) zwischen
`python companion/build_companion.py` und `tools/scan_companion.py` / `tools/stage_companion.py`:

```
build_companion.py  →  SIGN  →  signtool verify /pa /all  →  scan_companion  →  verify_companion  →  stage_companion (hasht)
```

Mit Zeitstempel (RFC 3161, z. B. `/tr <TSA-URL> /td sha256 /fd sha256`), damit die Signatur auch nach Ablauf des Zertifikats gültig bleibt.

Zusätzlich empfehlenswert: eine Versionsressource für die Companion-Exe (`--version-file` in PyInstaller) mit Produktname, Version und Herausgeber. Heute hat sie keine; TETHYS hat bereits Datei-Details.

### AETHER und TETHYS (vor dem Vendoring)
Beide werden nicht in diesem Repo gebaut. Signieren daher **im jeweiligen Engine-Repo bzw. beim Vendoring**:

```
Engine bauen  →  SIGN  →  verify  →  nach vendor/<engine>/ kopieren  →  ENGINE.json mit dem Hash der signierten Datei schreiben
```

- TETHYS: Signier-Schritt in `tools/update_tethys_vendor.py` vor der Hash-Berechnung (oder im THETYS-Release-Workflow).
- AETHER: gleicher Schritt im AETHER-Vendoring; `build_id` bleibt, `sha256`/`file_size` ändern sich.
- Ein erneutes Signieren einer bereits gevendorten Datei würde den Hash in `ENGINE.json` brechen; die bestehenden CI-Prüfungen fangen das ab ("staged AETHER Engine does not match vendor/aether/ENGINE.json").

### Neue Pflichtprüfung für Beta/Stable
In `build-release-candidate.yml` nach dem Paketbau für alle drei Windows-Exes im ZIP:
`Get-AuthenticodeSignature` muss `Valid` sein und der Signer der erwartete Herausgeber. Für Dev zunächst nur Warnung, damit Dev-Builds ohne Zertifikat weiterlaufen.

## 5. Zeitplan

| Datum | Schritt |
|---|---|
| 06.–07.11. | Anbieter wählen, Zertifikat bestellen, Identitätsprüfung starten |
| sobald ausgestellt | Secrets in GitHub hinterlegen; ich baue Signier-Schritt + Prüfung ein (Branch, kein Release) |
| vor 16.11. | Eine Dev-Version mit signierten Exes bauen und in deinem Blender prüfen (Datei-Eigenschaften → Digitale Signaturen; Tray-Kopie und Engine-Kopie laufen) |
| 16.11. Beta | Signierte Exes. Laut Release-Policy muss neue Release-Infrastruktur erst durch die Beta, bevor Stable sie nutzt |
| 28.11. Stable | Gleiches Zertifikat, gleiche Pipeline |

**Fallback, wenn das Zertifikat nicht rechtzeitig kommt:** Beta unsigniert ausliefern und in den Notes erwähnen. Dann wäre Signing für 3.0.0 Stable neue Infrastruktur ohne Beta-Durchlauf; entweder eine zweite Beta (z. B. kurz vor dem 28.11.) einplanen oder Signing auf 3.0.1 verschieben. Das entscheidest du.

## Quellen
- [Microsoft: Artifact Signing Quickstart (Voraussetzungen, Länder)](https://learn.microsoft.com/azure/artifact-signing/quickstart)
- [Microsoft Q&A: Einzelentwickler nur USA/Kanada](https://learn.microsoft.com/en-us/answers/questions/5915697/can-an-irish-sole-trader-(non-incorporated)-qualif)
- [SSL.com: Code-Signing-Zertifikate](https://www.ssl.com/code-signing/)
- [SSL.com: eSigner-Preise](https://ssl.com/guide/esigner-pricing-for-code-signing/)
- [SSL.com: Signieren aus CI/CD](https://ssl.com/esigner/)
- [JUCE-Forum: Azure-Signing für Plugin-Entwickler](https://forum.juce.com/t/azure-code-signing-for-plugin-developers-guide/60391?page=6)
