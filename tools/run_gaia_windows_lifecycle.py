# SPDX-License-Identifier: GPL-3.0-or-later
"""Serial real CUDA/Blender lifecycle gates, run from ordinary Windows shell.

No user Blender scene, persistent registry, solver source or binaries are edited.
Every test artifact lives in a fresh child of the caller's output directory.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.ppf.project_links import canonical_project_path
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
from cloth_next.updater.install_paths import ManagedSolverPaths
from cloth_next.updater.solver_registry import SolverInstallation, SolverRegistry, load_registry, write_registry
from tools.run_ppf_vertical_slice import run, _version_probe
from tools.run_ppf_sewing import run as sewing


def main():
    paths = ManagedSolverPaths.default()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solver", type=Path, default=paths.downloads_dir / "gaia-023-stitch-audit/official/target/cuda/release/ppf-cts-server.exe")
    parser.add_argument("--blender", type=Path, default=Path(r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/gaia-023-lifecycle-gates")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("this gate targets the approved Windows runtime")
    for executable in (args.solver, args.blender):
        if not executable.is_file(): parser.error(f"missing executable: {executable}")
    output = args.output_dir.resolve() / uuid.uuid4().hex[:12]
    output.mkdir(parents=True)
    registry_before = paths.registry_json.read_bytes() if paths.registry_json.is_file() else None
    registry = load_registry(paths.registry_json)
    report = {"result": "RUNNING", "output": str(output), "gates": []}
    report_path = output / "lifecycle-report.json"

    def fingerprint(root):
        files = []
        for source in (root / "frontend", root / "crates"):
            if source.is_dir():
                files.extend(path for path in source.rglob("*")
                             if path.is_file() and path.suffix != ".pyc")
        files.extend(root.glob("target/*/release/*.exe"))
        digest = hashlib.sha256()
        for path in sorted(set(files)):
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return {"files": len(files), "sha256": digest.hexdigest()}

    roots = {args.solver.resolve().parent.parent.parent.parent}
    roots.update(Path(item.root) for item in registry.installations if item.available)
    fingerprints = {str(root): fingerprint(root) for root in roots}
    report["runtime_fingerprints_before"] = fingerprints

    def record(name, data):
        report["gates"].append({"name": name, **data})
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(name, data.get("result", "PASS"), flush=True)

    def blender(script, name, options):
        command = [str(args.blender), "--background", "--factory-startup",
                   "--python-exit-code", "1", "--python", str(ROOT / "tools" / script), "--", *map(str, options)]
        with (output / (name+".log")).open("wb") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=1200, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0:
            raise RuntimeError(f"{name}: Blender failed ({result.returncode}); see {output / (name+'.log')}")

    def bake(executable, directory, name, resolution=None):
        with (output / (name + ".log")).open("w", encoding="utf-8") as log:
            with redirect_stdout(log):
                result = run(executable, directory, frame_count=32, resolution=resolution)
        assert result["result"] == "PASS" and len(result["solver_frames_fetched"]) == 31
        if result["protocol_version"] == "0.23":
            alias = canonical_project_path(Path(result["solver_executable"]), result["project_name"])
            assert not os.path.lexists(alias), f"alias leaked: {alias}"
            assert not (directory / "server-data" / result["project_name"]).exists()
        record(name, {"result": "PASS", "report": str(directory / "vertical_slice_report.json"),
                      "protocol": result["protocol_version"], "project": result["project_name"]})
        return result

    try:
        assert _version_probe(args.solver)[1:] == ("0.23", "2")
        blender("blender_project_link_long_path_probe.py", "native-blender-long-path", [ROOT])
        record("native-blender-long-path", {"result": "PASS"})
        first = bake(args.solver, output / "bake", "clean-cuda-bake")
        playback_report = output / "playback.json"
        blender("blender_gaia_playback_probe.py", "playback",
                ["--cache", first["pc2_path"], "--report", playback_report])
        record("blender-result-playback", json.loads(playback_report.read_text()))
        # After actual Blender/depsgraph readers close, prove Windows release.
        cache = Path(first["pc2_path"])
        moved = cache.with_suffix(".pc2.release-probe")
        os.replace(cache, moved); os.replace(moved, cache)
        record("pc2-release-after-playback", {"result": "PASS"})
        bake(args.solver, output / "second", "second-cuda-bake")
        bake(args.solver, output / "bake", "overwrite-rebake")

        blend, cache_root = output / "recovery-fixture.blend", output / "recovery-cache"
        for phase in ("cancel", "resume", "cancel", "fresh"):
            index = len(report["gates"])
            phase_report = output / (f"{index}-{phase}.json")
            blender("blender_recovery_integration.py", f"{index}-{phase}",
                    ["--phase", phase, "--repo", ROOT, "--blend", blend,
                     "--cache", cache_root, "--report", phase_report, "--solver", args.solver])
            value = json.loads(phase_report.read_text())
            expected = "cancelled" if phase == "cancel" else "finished"
            assert value["terminal"] == expected, value
            record("blender-restart-"+phase, {"result": "PASS", "report": str(phase_report)})

        for mode in ("intra", "cross", "mixed"):
            value = sewing(args.solver, output / ("sewing-"+mode), mode=mode)
            record("live-sewing-"+mode, {"result": "PASS", "report": str(output / ("sewing-"+mode) / "sewing_report.json")})

        # Only this temporary registry changes selection; user preferences stay byte-identical.
        package, protocol, schema = _version_probe(args.solver)
        official = args.solver.parent.parent.parent.parent
        registration = SolverInstallation("lifecycle-gaia-023", "Gaia 0.23", "official",
            str(official), str(args.solver), str(official / "frontend"), package, protocol, schema,
            "2026-09-27-20-44", False, True, True)
        isolated = registry.register(registration)
        temporary_registry = output / "registry.json"
        old = next((item for item in registry.installations if item.protocol_version == "0.22" and item.available), None)
        lumen = next((item for item in registry.installations if item.protocol_version == "0.18" and item.available), None)
        if old is None:
            raise RuntimeError("Gaia 0.22 switch gate cannot run: no existing installation")
        sequence = [registration, old, registration]
        if lumen:
            sequence += [lumen, registration]
        else:
            record("lumen-switch", {"result": "UNAVAILABLE", "reason": "Lumen is not installed; no download performed"})
        for index, selected in enumerate(sequence):
            isolated = isolated.select(selected.installation_id)
            write_registry(temporary_registry, isolated)
            selected = load_registry(temporary_registry).selected
            resolved = SolverResolver(_version_probe).resolve(SolverResolutionContext(selected_installation=selected))
            assert resolved and resolved.root_directory == selected.root
            value = bake(resolved.executable_path, output / f"switch-{index}",
                         f"switch-{index}-protocol-{selected.protocol_version}", resolution=resolved)
            assert value["protocol_version"] == selected.protocol_version
        report["result"] = "PASS"
    except BaseException:
        report["result"] = "FAIL"
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        registry_after = paths.registry_json.read_bytes() if paths.registry_json.is_file() else None
        report["persistent_registry_unchanged"] = registry_before == registry_after
        after = {str(root): fingerprint(root) for root in roots}
        report["runtime_fingerprints_after"] = after
        report["solver_sources_and_binaries_unchanged"] = fingerprints == after
        if registry_before != registry_after:
            report["result"] = "FAIL"
        if fingerprints != after:
            report["result"] = "FAIL"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("LIFECYCLE_REPORT="+str(report_path), flush=True)
    assert report["persistent_registry_unchanged"]
    assert report["solver_sources_and_binaries_unchanged"]
    print("LIFECYCLE PASS", flush=True)


if __name__ == "__main__":
    main()
