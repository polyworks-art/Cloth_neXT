# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run the real vertical slice after each persistent solver selection change."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cloth_next.ppf.compatibility import parse_executable_version
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
from cloth_next.updater.solver_registry import load_registry, write_registry
from tools.run_ppf_vertical_slice import run


def _probe(executable: Path) -> tuple[str, str, str]:
    result = subprocess.run([str(executable), "--version"], capture_output=True,
                            text=True, timeout=60, check=True)
    return parse_executable_version(result.stdout + result.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    registry = load_registry(args.registry)
    by_name = {item.display_name: item.installation_id
               for item in registry.installations}
    for index, name in enumerate(("Gaia", "Lumen", "Gaia"), 1):
        registry = registry.select(by_name[name])
        write_registry(args.registry, registry)
        registry = load_registry(args.registry)
        selected = registry.selected
        assert selected is not None and selected.display_name == name
        resolved = SolverResolver(_probe).resolve(SolverResolutionContext(
            selected_installation=selected))
        assert resolved is not None and resolved.executable_path is not None
        assert resolved.root_directory == selected.root
        report = run(resolved.executable_path, args.output_dir / f"{index}-{name}",
                     frame_count=3, resolution=resolved)
        assert report["protocol_version"] == selected.protocol_version
        assert report["schema_version"] == selected.schema_version
        assert report["result"] == "PASS"
        print(f"{index}: {name} protocol {report['protocol_version']} PASS", flush=True)


if __name__ == "__main__":
    main()
