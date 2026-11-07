#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package a native engine build (AETHER, TETHYS) and release it on the PolyWorks server.

The engines are no longer part of the add-on ZIP; the add-on downloads them
(docs/polyworks/NATIVE_ENGINE_DOWNLOADS_SERVER_SPEC.md). This tool writes the
engine package (engine.json + bin/<file>) and, unless --package-only, uploads it
with the release token exactly like tools/release_gaia.py:

    set CLOTH_NEXT_RELEASE_TOKEN=cnrel_...      (never print, store or commit it)
    python tools/release_engine.py aether "vendor/aether/windows-x64/AETHER Engine.exe" --min-addon 2.9.67
    python tools/release_engine.py tethys "vendor/tethys/windows-x64/TETHYS Engine.exe" --min-addon 2.9.67
    python tools/release_engine.py tethys vendor/tethys/linux-x64/tethys-engine --platform linux-x64 --min-addon 2.9.67

Version, build id and commit come from the binary (``--version``) and
vendor/<engine>/ENGINE.json; a binary for another platform is not run and needs
ENGINE.json (or --version/--build-id/--commit). Uploading releases the build
right away: 'dev' reaches dev add-ons (AETHER) or accounts on the dev release
list (TETHYS); 'public' everyone. Re-uploading the same version, platform,
backend and commit fails with 409 build_exists.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from cloth_next.updater import native_engines  # noqa: E402

_AETHER_LINE = re.compile(r"^AETHER Engine (\S+) \(build ([0-9a-f]{16}), commit (\S+)\)$")
_TETHYS_LINE = re.compile(r"^TETHYS Engine (\S+) protocol (\d+)$")


def _vendor(engine: str) -> dict:
    try:
        return json.loads((ROOT / "vendor" / engine / "ENGINE.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def identity(engine: str, binary: Path, platform: str, args) -> dict:
    """version, build_id, commit and the protocol fields for engine.json."""
    vendor = _vendor(engine)
    host = native_engines.current_platform()
    line = ""
    if platform == host:
        line = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                              timeout=30).stdout.strip()
    if engine == native_engines.AETHER:
        from cloth_next.aether_engine.client import API_VERSION, PROTOCOL_VERSION
        match = _AETHER_LINE.match(line)
        if platform == host and match is None:
            sys.exit(f"error: not an AETHER Engine binary: {line!r}")
        version = args.version or (match.group(1) if match else vendor.get("engine_version"))
        build_id = args.build_id or (match.group(2) if match else vendor.get("build_id"))
        commit = args.commit or (match.group(3) if match else vendor.get("engine_commit", ""))
        extra = {"protocol": PROTOCOL_VERSION, "api": list(API_VERSION)}
    else:
        from cloth_next.tethys.protocol import PROTOCOL_VERSION
        match = _TETHYS_LINE.match(line)
        if platform == host and (match is None or int(match.group(2)) != PROTOCOL_VERSION):
            sys.exit(f"error: not a TETHYS Engine binary for protocol {PROTOCOL_VERSION}: {line!r}")
        if match is None and platform != host:
            # Same engine commit (vendor/tethys/ENGINE.json): the host build names the version.
            sibling = ROOT / "vendor" / "tethys" / host / native_engines.spec(engine).filename(host)
            if sibling.is_file():
                match = _TETHYS_LINE.match(subprocess.run([str(sibling), "--version"], capture_output=True,
                                                          text=True, timeout=30).stdout.strip())
        version = args.version or (match.group(1) if match else None)
        commit = args.commit or vendor.get("engine_commit", "")
        build_id = args.build_id or commit[:16]
        extra = {"protocol": PROTOCOL_VERSION}
    if not version or not build_id:
        sys.exit("error: version or build id unknown; pass --version and --build-id")
    return {"version": version, "build_id": build_id, "commit": commit or "", "extra": extra}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("engine", choices=sorted(native_engines.SPECS))
    parser.add_argument("binary", type=Path)
    parser.add_argument("--platform", default=None, choices=["windows-x64", "linux-x64"])
    parser.add_argument("--min-addon", help="lowest Cloth NeXt version this build is for (required to upload)")
    parser.add_argument("--max-addon")
    parser.add_argument("--audience", choices=["dev", "public"], default="dev")
    parser.add_argument("--notes", help="release notes (text) or @file")
    parser.add_argument("--version", help="override the engine version")
    parser.add_argument("--build-id", help="override the engine build id")
    parser.add_argument("--commit", help="override the engine source commit")
    parser.add_argument("--output", type=Path, help="package path (default dist/<engine>-<version>-<platform>.zip)")
    parser.add_argument("--package-only", action="store_true", help="write the package, upload nothing")
    parser.add_argument("--api", default=os.environ.get("CLOTH_NEXT_API", "https://account.cloth-next.de/api"))
    args = parser.parse_args()

    spec = native_engines.spec(args.engine)
    platform = args.platform or native_engines.current_platform()
    if spec.filename(platform) is None:
        sys.exit(f"error: the {spec.label} has no {platform} build")
    if not args.binary.is_file():
        sys.exit(f"error: no file {args.binary}")
    found = identity(args.engine, args.binary, platform, args)
    output = args.output or ROOT / "dist" / f"{args.engine}-engine-{found['version']}-{platform}.zip"
    manifest = native_engines.build_package(
        args.engine, args.binary, output, version=found["version"], build_id=found["build_id"],
        commit=found["commit"], platform=platform, extra=found["extra"])
    print(f"package {output} ({output.stat().st_size} bytes, engine {manifest['version']}, "
          f"build {manifest['build_id']}, binary sha256 {manifest['sha256']})", file=sys.stderr)
    if args.package_only:
        return
    if not args.min_addon:
        sys.exit("error: --min-addon is required to upload")
    token = os.environ.get("CLOTH_NEXT_RELEASE_TOKEN", "")
    if not token:
        sys.exit("error: set CLOTH_NEXT_RELEASE_TOKEN")
    if not found["commit"]:
        sys.exit("error: the engine commit is unknown; pass --commit")

    import hashlib
    from tools.release_gaia import request
    data = output.read_bytes()
    local_sha = hashlib.sha256(data).hexdigest()
    with output.open("rb") as stream:
        upload = request(args.api, "PUT",
                         f"/v1/release/solver/uploads?filename={urllib.parse.quote(output.name)}",
                         token, body=stream, length=len(data), content_type="application/octet-stream")
    if upload["sha256"] != local_sha or upload["size_bytes"] != len(data):
        sys.exit(f"error: server received a different file (sha256 {upload['sha256']}, "
                 f"{upload['size_bytes']} bytes)")
    notes = args.notes
    if notes and notes.startswith("@"):
        notes = Path(notes[1:]).read_text(encoding="utf-8").strip()  # server rejects control characters
    build = request(args.api, "POST", "/v1/release/solver/builds", token, body=json.dumps({
        "upload_id": upload["upload_id"], "engine": args.engine, "version": found["version"],
        "platform": platform, "backend": spec.backend, "min_addon_version": args.min_addon,
        "max_addon_version": args.max_addon, "source_revision": found["commit"].lower(),
        "release_notes": notes, "audience": args.audience}).encode())
    print(json.dumps(build, indent=2))


if __name__ == "__main__":
    main()
