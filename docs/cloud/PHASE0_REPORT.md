# Cloud Bakes – Phase-0-Bericht (technischer Durchstich)

Stand 23.10.2026, Branch `claude/project-thread-fd9edx`, Add-on 2.9.39-Stand (`038dda7` + Cloud-Commits).
Blender 5.2.3, Windows 11, eine GPU (dieser PC). Rohdaten: `docs/validation/cloud-phase0/*.json`.

## Wichtigste Einordnung

Der „Server“ ist in Phase 0 der deterministische In-Process-Testserver (`tests/cloud_backend.py`), der
das hochgeladene Paket **mit dem echten Solver auf diesem PC** ausführt. Bewiesen ist damit der Weg
Export → Paket → Übertragung (fortsetzbar, Dedup, idempotent) → echter Solver-Lauf nur aus den
Paketdateien → Rohausgabe → Download mit Hash-Prüfung → Import nur aus dem Snapshot.
**Nicht** bewiesen ist eine echte Cloud-Ausführung: es gibt noch keinen `/v1/cloud/`-Vertrag und keinen
Server. Unterschiede zwischen GPUs/Treibern (E14) konnten nicht gemessen werden.

## Läufe (Schritte 1–7, `tools/cloud_phase0.py`)

| Szene | Engine | Objekte / Vertices | Paket | Lokaler Bake | Server-Lauf | Rohausgabe | Lokal ↔ Cloud-Weg |
|---|---|---|---|---|---|---|---|
| Tuch auf Kugel, 24 Frames | GAIA | 1 / 625 | 130 KB | 235 s | 195 s | 174 KB | bis Kontakt bitgleich, danach max 12 cm, Ø 7 mm |
| Zwei Tücher, Kontakt, **Reproducible** | GAIA | 2 / 1.250 | 213 KB | 178 s | 271 s | 349 KB | **bitgleich** |
| Tuch auf Kugel, 24 Frames | AETHER 2026-10-13 | 1 / 625 | 129 KB | 394 s | 251 s | 174 KB | bis Kontakt bitgleich, danach max 13 cm, Ø 9 mm |
| Soft Body + Tuch + Kugel | GAIA | 2 / 297 | 75 KB | 329 s | 320 s | 713 KB (mit Surface Map) | Header gleich, max 31 cm nach Kontakt |
| Nähte (Cross + Same-Object) | GAIA | 3 / 515 | 95 KB | 899 s | 602 s | 144 KB | max 1,3 m (chaotisch, Stärke 100) |
| Nähte, **Reproducible** | GAIA | 3 / 515 | 95 KB | 710 s | 282 s | 144 KB | **bitgleich** |
| Water Flow, Hydrodynamic Pressure, Tauchszene 50–60 | GAIA | 1 / 2.688 | 142 MB | 197 s | 155 s | 330 KB | max 0,2 µm (Float-Rauschen) |

In allen Läufen: PC2-Header, Vertex- und Frame-Zahl identisch, Sample 0 bitgleich, Sidecar `READY`,
ausgeführte `build_id` = angeforderte (`gaia-0.26.0-dev.20261022+cuda12.8.windows-x64.cc9b0c13ec2f`
bzw. `aether:2026-10-13-12-32`). Der Import lief jeweils nach `read_factory_settings` (leere Szene).

**Toleranz (T6):** Wo der Solver deterministisch läuft (GAIA Reproducible), ist das Ergebnis bitgleich.
Ohne Reproducible ist die Lauf-zu-Lauf-Streuung desselben Solvers auf derselben GPU ab dem ersten
Kontakt groß (cm bis m bei Nähten). Ein Cloud-Vergleich gegen lokal kann deshalb nur gegen diese
Streuung bewertet werden, nicht gegen eine feste Millimeter-Toleranz. Vorschlag: Abnahme =
bitgleich mit Reproducible; ohne Reproducible gleiche Header/Frames und Sample 0 bitgleich.

Zeiten sind Wanduhrzeiten auf einem PC, der parallel die Test-Suite bzw. andere Last hatte; sie
sind nur Größenordnungen. Übertragungszeiten des Testservers (in-process) sind ohne Aussagekraft.

## Befunde, die das Konzept ergänzen

1. **Paket braucht `official-bindings`** (Nähte, Face-Friction, Material Zones), die nach dem BUILD
   vom Python der Solver-Installation angewendet werden. Auch AETHER 0.26 nutzt sie.
2. **Water Flow mit Hydrodynamic Pressure** (Standard) läuft über vorbereitete Volumen, nicht über
   die native Fluid-Sequenz: **≈ 11,8 MB pro Frame** (12 Frames = 141 MB). Für 250 Frames ≈ 3 GB
   Upload. Die Vorbereitung läuft lokal (24–26 s für 12 Frames) und braucht die lokale GAIA-Installation.
   Der FLIP-Cache selbst wird nie übertragen; die Provenienz mit lokalem FLIP-Pfad wird bereinigt.
3. **Native Flow** (`native-file`) wird genauso paketiert (kodierte Param-Datei), ist aber in Phase 0
   nicht real gelaufen. Der alte Held-Stream (Protokoll 0.23) ist für die Cloud abgelehnt.
4. **Objektnamen** stehen bereits im Scene-Payload (`data`), P4 betrifft also auch den Solver-Input.
5. **Topologie-Signatur** erkennt umsortierte Vertices (Test vorhanden); keine eigene Reihenfolge-Signatur nötig.
6. **Zwei Fehler im eigenen Code gefunden und behoben:** fehlender Arbeitsordner beim Paketieren mit
   Wasser; Solver-Sitzung wollte fremde (vom Aufrufer gelieferte) Hydro-Volumen löschen – die
   Sicherheitsprüfung `delete_owned` hat das korrekt verhindert.

## Kompression (E21, `tools/cloud_compression_bench.py`)

Blenders Python 5.2 bringt `zstandard` 0.25 mit → keine neue Abhängigkeit nötig. Streaming in
4-MiB-Blöcken, Spitzen-Python-Speicher ≤ 17 MiB. Netto = Kompression + Senden + Dekompression gegen
unkomprimiertes Senden bei 50 Mbit/s:

| Datei | Größe | zlib-6 Faktor / Netto | zstd-1 Faktor / Netto | zstd-1 MB/s (komp./dekomp.) |
|---|---|---|---|---|
| Hydro-Volumen (12 Frames) | 141 MB | 2,23× / −37 % | **2,30× / −52 %** | 306 / 266 |
| `data` (Szenen) | 64–400 KB | 4,5–6,9× / −61…−72 % | **4,5–14× / −75…−91 %** | 280–435 / 400–680 |
| `param` (Wasser) | 31 KB | 17× / −87 % | 22× / −92 % | 259 / 479 |
| Rohframes (Download) | 0,14–0,7 MB | 1,1–1,36× / −11…+17 % | 1,08–1,35× / −4…−21 % | 320–510 / 480–685 |

zstd-19 und zlib-9 sind durchweg netto langsamer. **Empfehlung:** zstd Stufe 1 als Transport-
kompression für Upload-Dateien (`data`, `param`, Hydro-Volumen); Rohframes unkomprimiert lassen
(Gewinn ≤ 21 %, oft kleiner). Kriterium aus 5.3 (≥ 15–20 % netto bei 50 Mbit/s, < 256 MiB) für
Uploads erfüllt. Noch **nicht eingeschaltet**: braucht Unterstützung im Backend-Vertrag
(`transport.compression` im Manifest ist vorbereitet, Hashes bleiben über die Rohbytes).

## Status je Schritt

| Schritt | Status |
|---|---|
| 1 Export | echt, Produktionscode (`bpy.ops.clothnext.bake`), Plan abgegriffen |
| 2 Paket + Snapshot | echt; unveränderlicher Speicher, Privacy-Scan, Snapshot-Digest |
| 3 Übertragung | nur Testserver; Abbruch/Fortsetzen, Dedup pro Konto, verlorene Submit-/Create-Antwort, Neustart: automatisierte Tests |
| 4 Ausführung | echter GAIA- und AETHER-Lauf **auf diesem PC** aus den Paketdateien |
| 5 Rohausgabe | echt; Vertex-Map, Surface-Map, Frame-Blöcke à 25 mit SHA-256; korrupter Block wird erkannt (Test) |
| 6 Import | echt, nur Snapshot + Rohausgabe, gemeinsamer Produktionspfad |
| 7 Vergleich | siehe Tabelle |

## Grundlage für E25 (Abrechnung/Aufbewahrung)

Server-Laufzeiten auf einer Desktop-GPU: 155–600 s für 11–24 Frames kleiner Szenen; Speicher pro Job
ohne Wasser < 1 MB Ein- und Ausgabe, mit Hydrodynamic Pressure ≈ 12 MB pro Frame Eingabe. Die
Wasser-Eingabe dominiert Speicher- und Transferkosten mit Abstand.
