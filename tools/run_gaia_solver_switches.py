# SPDX-License-Identifier: GPL-3.0-or-later
"""Real 0.23/0.22/0.23 switch gates with immutable isolated runtime copies.

Separate from the full lifecycle runner: never relabel a failed lifecycle
report as PASS. The combined evidence must cite both reports and their scopes.
"""
from contextlib import redirect_stdout
from dataclasses import replace
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.ppf.project_links import canonical_project_path
from cloth_next.ppf.resolver import SolverResolver, SolverResolutionContext
from cloth_next.updater.install_paths import ManagedSolverPaths
from cloth_next.updater.solver_registry import SolverInstallation, SolverRegistry, load_registry, write_registry
from tools.run_ppf_vertical_slice import run, _version_probe


def fingerprint(root):
    files = [path for folder in (root / "frontend", root / "crates")
             for path in folder.rglob("*") if path.is_file() and path.suffix != ".pyc"]
    files += list(root.glob("target/*/release/*.exe"))
    digest = hashlib.sha256()
    for path in sorted(set(files)):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return {"files": len(files), "sha256": digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gaia-023-root", type=Path, required=True)
    parser.add_argument("--gaia-022-root", type=Path, required=True)
    args = parser.parse_args()
    paths = ManagedSolverPaths.default()
    original = paths.registry_json.read_bytes()
    registry = load_registry(paths.registry_json)
    installed = next(item for item in registry.installations
                     if item.protocol_version == "0.22" and item.available)
    root22, root23 = args.gaia_022_root.resolve(), args.gaia_023_root.resolve()
    old = replace(installed, root_path=str(root22),
                  executable_path=str(root22 / installed.executable.relative_to(installed.root)),
                  frontend_path=str(root22 / "frontend"))
    executable23 = root23 / "target/cuda/release/ppf-cts-server.exe"
    package, protocol, schema = _version_probe(executable23)
    assert (protocol, schema) == ("0.23", "2")
    new = SolverInstallation("switch-gaia-023", "Gaia 0.23", "official", str(root23),
        str(executable23), str(root23 / "frontend"), package, protocol, schema,
        "2026-09-27-20-44", False, True, True)
    roots = {installed.root, root22, root23}
    before = {str(root): fingerprint(root) for root in roots}
    assert before[str(installed.root)] == before[str(root22)], "0.22 copy differs from installed runtime"
    output = ROOT / "dist/gaia-023-solver-switches" / uuid.uuid4().hex[:12]
    output.mkdir(parents=True)
    report = {"scope": "isolated-real-solver-switch-only", "result": "RUNNING", "gates": [],
              "runtime_fingerprints_before": before,
              "installed_022_copy_is_byte_identical": True,
              "lumen": "UNAVAILABLE: not installed; no download performed"}
    report_path = output / "switch-report.json"
    isolated = SolverRegistry((new, old))
    try:
        for index, chosen in enumerate((new, old, new)):
            selected = isolated.select(chosen.installation_id)
            registry_path = output / "registry.json"
            write_registry(registry_path, selected)
            chosen = load_registry(registry_path).selected
            resolved = SolverResolver(_version_probe).resolve(
                SolverResolutionContext(selected_installation=chosen))
            assert resolved and resolved.root_directory == chosen.root
            directory = output / f"switch-{index}"
            with (output / f"switch-{index}.log").open("w", encoding="utf-8") as log:
                with redirect_stdout(log):
                    value = run(resolved.executable_path, directory, frame_count=32, resolution=resolved)
            assert value["result"] == "PASS" and value["protocol_version"] == chosen.protocol_version
            assert len(value["solver_frames_fetched"]) == 31
            if chosen.protocol_version == "0.23":
                alias = canonical_project_path(resolved.executable_path, value["project_name"])
                assert not os.path.lexists(alias)
                assert not (directory / "server-data" / value["project_name"]).exists()
            report["gates"].append({"index": index, "protocol": chosen.protocol_version,
                                    "result": "PASS", "report": str(directory / "vertical_slice_report.json")})
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"SWITCH {index} protocol {chosen.protocol_version} PASS", flush=True)
        report["result"] = "PASS"
    except BaseException:
        report["result"] = "FAIL"
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        after = {str(root): fingerprint(root) for root in roots}
        report["runtime_fingerprints_after"] = after
        report["solver_sources_and_binaries_unchanged"] = before == after
        report["persistent_registry_unchanged"] = paths.registry_json.read_bytes() == original
        if before != after or not report["persistent_registry_unchanged"]:
            report["result"] = "FAIL"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("SWITCH_REPORT=" + str(report_path), flush=True)
    assert report["result"] == "PASS"
    print("SWITCH PASS", flush=True)


if __name__ == "__main__":
    main()
