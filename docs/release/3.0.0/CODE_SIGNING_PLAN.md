# Code-Signing für Cloth NeXt 3.0 — Entscheidung und Plan ohne Kosten

Stand: 2026-11-06. **Entscheidung (Tim, 06.11.): keine kostenpflichtige Signatur.** Beta 2.10.0 und Stable 3.0.0 werden unsigniert ausgeliefert.

## 1. Betroffene Programme

| Programm | Herkunft | Hash-Pinning |
|---|---|---|
| `Cloth NeXt Bake.exe` (Companion: Bake-Fenster, Tray, Konto, Welcome/What's New) | PyInstaller `--onefile`, gebaut in CI | `companion_manifest.json` |
| `AETHER Engine.exe` | privates Repo `polyworks-art/AETHER`, gevendort | `vendor/aether/ENGINE.json` |
| `TETHYS Engine.exe` | privates Repo `polyworks-art/THETYS`, gevendort | `vendor/tethys/ENGINE.json`, Laufzeitprüfung in `cloth_next/tethys/engine.py` |

## 2. Was ohne Signatur passieren kann

- Blender lädt die Extension per Python, die Exes haben meist kein "Mark of the Web". Der SmartScreen-Dialog ist daher selten das Problem.
- Realistische Risiken: **Fehlalarme von Virenscannern** (vor allem die PyInstaller-onefile-Companion), **Smart App Control** unter Windows 11 (falls beim Nutzer aktiv, kann es unsignierte Programme blockieren) und Firmen-Richtlinien.
- Herausgeber erscheint als "Unbekannt".

## 3. Kostenlose Maßnahmen (Empfehlung für Beta/Stable)

1. **Datei-Details für die Companion-Exe** (PyInstaller `--version-file`: Produkt, Version, Beschreibung, Copyright). TETHYS hat sie schon; AETHER prüfen und ggf. ergänzen. Kostet nichts und hilft Scannern und Support.
2. **Exes bei Microsoft als Fehlalarm einreichen**, falls Defender anschlägt: kostenlos über das Microsoft Security Intelligence Portal ("Submit a file", Software-Entwickler). Am besten direkt nach dem Beta-Build vorsorglich alle drei Exes einreichen.
3. **Beta-Build bei VirusTotal prüfen** (nur Hash-Abfrage oder Upload der drei Exes; die Dateien sind dann für VirusTotal-Partner einsehbar, bei den privaten Engines abwägen). Treffer vor dem Stable melden.
4. **Companion-Build robuster gegen Fehlalarme:** kein UPX (aktuell nicht genutzt, so lassen); eine `--onedir`-Variante wird seltener falsch erkannt, wäre aber eine größere Umstellung (Policy erlaubt nur eine Companion-Exe) und ist für 3.0 nicht nötig.
5. **Hinweis für Nutzer** in FAQ/Superhive-Seite: was tun, wenn Virenscanner oder Smart App Control die Exe blockiert (Ausnahme setzen, Pfad nennen), plus SHA-256 aus dem Manifest zum Abgleich.

## 4. Optional später (kostenlos, aber unsicher)

- **SignPath Foundation** signiert Open-Source-Projekte kostenlos (Herausgeber erscheint dann als SignPath Foundation, Build muss in CI laufen). Infrage käme nur die Companion aus dem öffentlichen GPL-Repo; AETHER und TETHYS sind privat und damit ausgeschlossen. Ob ein Add-on mit gebündelten proprietären Engines angenommen wird, ist offen; Antrag dauert. Nicht für 3.0.
- Selbst-signierte Zertifikate bringen bei fremden Rechnern nichts (keine Vertrauenskette) und sind deshalb keine Option.

## 5. Falls später doch signiert wird

Reihenfolge bleibt: bauen → signieren (mit Zeitstempel) → prüfen → erst dann hashen bzw. vendoren. Details siehe Git-Historie dieser Datei (erste Fassung vom 06.11.).
