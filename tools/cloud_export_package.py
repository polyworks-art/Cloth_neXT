# SPDX-License-Identifier: GPL-3.0-or-later
"""Copy a real exported Cloud package out of the PackageStore, verified.

    python tools/cloud_export_package.py <phase0 run dir> <solver root> <output dir>

Writes ``manifest.json`` (the exact canonical bytes the add-on stores),
``files/<role>/<name>`` for every manifest entry, ``SHA256SUMS``,
``identity.json`` (add-on commit, Blender, engine build, full hashes of the
native binaries that ran), the local Bake report, the local reference PC2
and the snapshot-only Cloud import, and ``validation.json`` (sizes, SHA-256,
OpenAPI ``Create`` schema, snapshot load, privacy scan).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.cloud import privacy  # noqa: E402
from cloth_next.cloud.snapshot import load_snapshot  # noqa: E402


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    run, solver_root, out = (Path(arg).resolve() for arg in sys.argv[1:4])
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    store = run / "account" / "cloud-packages" / "blobs"
    registry = json.loads((run / "account" / "cloud-jobs.json").read_text(encoding="utf-8"))
    record = registry["jobs"][0]
    manifest_blob = (store / record["manifest_sha256"][:2] / record["manifest_sha256"]).read_bytes()
    manifest = json.loads(manifest_blob)
    if out.exists():
        shutil.rmtree(out)
    (out / "files").mkdir(parents=True)
    (out / "manifest.json").write_bytes(manifest_blob)
    v1_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    sums, checks = [], {}
    for item in manifest["files"]:
        blob = (store / item["sha256"][:2] / item["sha256"]).read_bytes()
        ok = len(blob) == item["size_bytes"] and sha256(blob) == item["sha256"]
        checks[f"{item['role']}/{item['name']}"] = ok
        target = out / "files" / item["role"] / item["name"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        sums.append(f"{item['sha256']}  files/{item['role']}/{item['name']}")
    sums.append(f"{sha256(manifest_blob)}  manifest.json")
    snapshot_item = next(item for item in manifest["files"] if item["role"] == "import-snapshot")
    snapshot = load_snapshot((out / "files" / "import-snapshot" / snapshot_item["name"]).read_bytes(),
                             expected_digest=manifest["import_snapshot_digest"])

    import jsonschema
    spec = json.loads((ROOT / "tests" / "fixtures" / "cloud_v1" / "openapi.json")
                      .read_text(encoding="utf-8"))
    jsonschema.validate({"manifest": manifest},
                        {"$ref": "#/components/schemas/Create", "components": spec["components"]},
                        format_checker=jsonschema.FormatChecker())

    executable = solver_root / "target" / "cuda" / "release" / "ppf-cts-server.exe"
    worker = executable.with_name("ppf-contact-solver.exe")
    identity = {
        "addon_branch": "claude/project-thread-fd9edx",
        "addon_commit": report.get("addon_commit"),
        "blender": report["blender"],
        "engine": manifest["engine"], "build_id": manifest["build_id"],
        "solver_version": manifest["solver_version"],
        "backend": "cuda (target/cuda/release, Windows x64)",
        "solver_executable": "target/cuda/release/ppf-cts-server.exe",
        "solver_sha256": sha256(executable.read_bytes()),
        "worker_executable": "target/cuda/release/ppf-contact-solver.exe",
        "worker_sha256": sha256(worker.read_bytes()),
        "manifest_solver_sha256": manifest["solver_sha256"],
        "manifest_worker_sha256": manifest["worker_sha256"],
        "manifest_sha256_v1_rule": sha256(v1_bytes),
        "manifest_file_sha256": sha256(manifest_blob),
        "expected_native_raw_frames": manifest["frame_end"] - manifest["frame_start"],
    }
    (out / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
    (out / "local-bake-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    reference = out / "reference"
    reference.mkdir()
    for pc2 in sorted((run / "local").glob("cn_test_cloth_*.pc2")):
        shutil.copy2(pc2, reference / ("local-" + pc2.name))
        meta = pc2.with_suffix(".meta.json")
        if meta.is_file():
            shutil.copy2(meta, reference / ("local-" + meta.name))
    for pc2 in sorted((run / "cloud").glob("*.pc2")):
        shutil.copy2(pc2, reference / ("snapshot-import-" + pc2.name))
        meta = pc2.with_suffix(".meta.json")
        if meta.is_file():
            shutil.copy2(meta, reference / ("snapshot-import-" + meta.name))
    sums += [f"{sha256(path.read_bytes())}  {path.relative_to(out).as_posix()}"
             for path in sorted(reference.iterdir())]
    hits = privacy.findings(manifest) + privacy.findings(snapshot.document)
    validation = {
        "all_sizes_and_hashes_match": all(checks.values()), "files": checks,
        "manifest_matches_openapi_create": True,
        "manifest_sha256_equals_v1_canonical_rule": sha256(v1_bytes) == sha256(manifest_blob),
        "snapshot_loads_with_declared_digest": True,
        "snapshot_objects": [{"uuid": item["uuid"], "vertex_count": item["vertex_count"]}
                             for item in snapshot.objects],
        "snapshot_arrays_files": 0,
        "snapshot_arrays_note": "initial positions are inside the single import-snapshot file",
        "fluid_file": False, "water_mode": manifest["water_mode"],
        "privacy_findings": hits,
        "native_hashes_match_manifest": (
            identity["solver_sha256"] == manifest["solver_sha256"]
            and identity["worker_sha256"] == manifest["worker_sha256"]),
        "phase0_comparison": report["steps"]["7_compare"],
    }
    (out / "validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(json.dumps({key: validation[key] for key in (
        "all_sizes_and_hashes_match", "manifest_sha256_equals_v1_canonical_rule",
        "privacy_findings", "native_hashes_match_manifest")}))
    return 0 if (validation["all_sizes_and_hashes_match"] and not hits
                 and validation["native_hashes_match_manifest"]) else 1


if __name__ == "__main__":
    sys.exit(main())
