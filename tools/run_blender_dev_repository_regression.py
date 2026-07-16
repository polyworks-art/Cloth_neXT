# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender 5.1.2 duplicate-id Dev repository regression.

Serves two official Blender repositories: one with duplicate ``cloth_next``
records for Dev 1..5 and one repaired single Dev 5 record. Each phase uses a
fresh isolated Blender profile so no production profile or extension is
touched. The duplicate phase records ambiguity or a version mismatch. The
repaired phase requires repository, installed, and loaded versions to agree
and therefore no update to be offered.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import shutil
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROBE = Path(__file__).with_name("blender_dev_repository_probe.py")


def _run(blender: Path, profile: Path, url: str, index: Path,
         result: Path, expected: str) -> dict:
    env = os.environ.copy()
    env["BLENDER_USER_RESOURCES"] = str(profile)
    command = [str(blender), "--factory-startup", "--background",
               "--online-mode", "--python", str(PROBE), "--",
               "--url", url, "--index", str(index), "--result", str(result),
               "--expected", expected]
    subprocess.run(command, check=True, env=env, shell=False)
    return json.loads(result.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--duplicate-repo", type=Path, required=True)
    parser.add_argument("--repaired-repo", type=Path, required=True)
    parser.add_argument("--expected", default="0.3.21")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    root = Path(os.path.commonpath([args.duplicate_repo.resolve(),
                                    args.repaired_repo.resolve()]))
    handler = lambda *a, **kw: SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(root), **kw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temp_text:
            temp = Path(temp_text)
            port = server.server_address[1]
            duplicate_url = (f"http://127.0.0.1:{port}/"
                             f"{args.duplicate_repo.name}/index.json")
            repaired_url = (f"http://127.0.0.1:{port}/"
                            f"{args.repaired_repo.name}/index.json")
            profile = temp / "blender-profile"
            duplicate = _run(args.blender, profile,
                             duplicate_url, args.duplicate_repo / "index.json",
                             temp / "duplicate.json", args.expected)
            # Reproduce the required recovery boundary: changing the served
            # index is insufficient for a profile that cached duplicates.
            # Invalidate that exact repository cache while Blender is closed,
            # then synchronize and reinstall from the repaired repository.
            cache = Path(duplicate["repo_directory"]) / ".blender_ext"
            if cache.exists():
                shutil.rmtree(cache)
            repaired = _run(args.blender, profile,
                            repaired_url, args.repaired_repo / "index.json",
                            temp / "repaired.json", args.expected)
    finally:
        server.shutdown()
        server.server_close()

    if duplicate["duplicate_count"] < 2:
        raise AssertionError("duplicate fixture did not contain duplicate ids")
    if not (duplicate["duplicate_count"] > 1 or
            duplicate["installed_manifest"] != duplicate["repository_candidate"]):
        raise AssertionError("duplicate repository ambiguity was not recorded")
    for key in ("repository_candidate", "installed_manifest", "loaded_manifest"):
        if repaired[key] != args.expected:
            raise AssertionError(f"repaired {key} is {repaired[key]!r}")
    if repaired["duplicate_count"] != 1 or repaired["update_offered"]:
        raise AssertionError("repaired repository still offers an update")
    report = {"duplicate": duplicate, "repaired": repaired}
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
