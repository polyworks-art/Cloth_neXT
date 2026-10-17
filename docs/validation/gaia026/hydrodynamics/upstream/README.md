# Reproduktion des isolierten nativen Experiments

Basiscommit im offiziellen ppf-contact-solver:
`7232f4033515e1e72359f4ce5145456ce7d156f7`.

Der danebenliegende Patch enthält nur Source-Änderungen. Im separaten sauberen
Checkout mit `git apply --check` prüfen, anschließend `git apply` verwenden.
Nicht auf eine installierte Solver-Version anwenden.

Mit Rust/Cargo, Python 3 als `python3`, MSVC x64 und passenden Windows-SDK-Libraries:

```powershell
$env:CARGO_TARGET_DIR = "$PWD/target/hydrodynamics-cpu"
cargo test -p ppf-cts-solver --features cpu --no-default-features
```

Die lokalen `build-hydrodynamics-cpu.cmd`/`build-hydrodynamics-cuda.cmd` im
Entwicklungscheckout setzen die portable Rust-Installation unter `.tools`,
das isolierte Target-Verzeichnis und die VC-Umgebung. Keine globale Installation
oder Solver-Auswahl wurde geändert. CPU-Build und native Tests sind geprüft;
der CUDA-Build ist wegen der fehlenden Upstream-Bootstrap-Library nicht geprüft.

Der Testeingang ist die ausschließlich pro Testprozess gesetzte Variable
`PPF_EXPERIMENTAL_HYDRO_TEST_JSON`, beispielsweise:

```json
{
  "density_kg_m3": 1000.0,
  "normal_coefficient": 1.0,
  "tangential_coefficient": 0.0,
  "wet_area_fraction": 1.0,
  "velocity_m_s": [0.2, 0.0, 0.0]
}
```

Die Geschwindigkeit ist physikalisch in m/s in Solver-World-Achsen. Geometrie
muss in Metern vorliegen, world_scaling=1. Der vorgegebene Wet-Area-Wert wird
auf alle dynamischen Shell-Triangles angewandt; er ist kein FLIP-Adapter.
Ohne die Variable ist der neue Pass aus. Keinen solchen Eingang zusammen mit
einem zusätzlich aktiven alten Fluid-Flow-Term verwenden, wenn die Effekte
verglichen werden sollen: Beide Kräfte würden sonst addiert.

`tools/validate_hydrodynamic_native.py` im Cloth-NeXt-Workspace exportiert die
synthetischen Szenen mit dem offiziellen Frontend, startet aber ausschließlich
das über `--solver` benannte experimentelle Executable direkt. `App.set_backend`
im Skript bestimmt nur die Identität des Exportwerkzeugs. Die generierte
offizielle Startdatei wird nicht aufgerufen. Elf native Fälle, analytische
Vergleiche, Mesh-/Zeitschrittprüfung und tatsächliche Colliderkontakte werden
protokolliert. Der Bericht enthält den SHA-256 des gestarteten Executables.

Die kanonischen erfolgreichen Integrationsbelege liegen in `../native-cpu-final/`.
Frühere `native-cpu-contact*`-Ordner dokumentieren zusätzlich die Korrektur der
Testauswertung: Native Vertex-Reihenfolge kann umsortiert sein, und vollständig
gepinnte Collider gehören zur separaten Collision Mesh. Diese frühen Abbrüche
waren Auswertungsfehler und sind keine zusätzlich bestandenen Testläufe.

`../native-regressions.log`: 578 bestanden, 9 ignoriert.
`../addon-regressions.log`: 175 bestanden.
`../newton-diagnostics.json`: aus den finalen nativen Prozesslogs extrahiert;
lineare Residuen sind nicht mit nichtlinearen Gesamtresiduen gleichzusetzen.
`../verification-summary.json`: Ausführungs- und Freigabegrenzen.
