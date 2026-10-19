#!/usr/bin/env python3
"""Upload a GAIA build and release it (dev or public) with a release token. Standard library only.

    export CLOTH_NEXT_RELEASE_TOKEN=cnrel_...
    python3 release_gaia.py dist/gaia-linux-x64-cuda12.8.tar.zst \
        --version 3.1.0 --platform linux-x64 --backend cuda12.8 --min-addon 3.0.0 [--audience dev]

--source-revision defaults to `git rev-parse HEAD` in the current directory. Uploading releases the
build right away: 'dev' reaches only accounts on the dev release list, 'public' everyone with GAIA access.
The token cannot delete builds or change existing releases; that stays in administration.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

DEFAULT_API = "https://account.cloth-next.de/api"


def request(api: str, method: str, path: str, token: str, body=None, length: int | None = None,
            content_type: str = "application/json") -> dict:
    u = urllib.parse.urlsplit(api)
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(u.netloc, timeout=3600)
    headers = {"X-Release-Token": token, "Content-Type": content_type, "Accept": "application/json"}
    if length is not None:
        headers["Content-Length"] = str(length)
    conn.request(method, u.path.rstrip("/") + path, body=body, headers=headers)
    r = conn.getresponse()
    raw = r.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = {"raw": raw[:500].decode(errors="replace")}
    if r.status != 200:
        err = data.get("error", data) if isinstance(data, dict) else data
        sys.exit(f"error: {method} {path} -> HTTP {r.status}: {err}")
    return data


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", type=Path)
    p.add_argument("--version", required=True)
    p.add_argument("--platform", required=True, choices=["linux-x64", "windows-x64"])
    p.add_argument("--backend", required=True, help="for example cuda12.8 or cpu")
    p.add_argument("--min-addon", required=True)
    p.add_argument("--max-addon")
    p.add_argument("--source-revision", help="default: git rev-parse HEAD")
    p.add_argument("--audience", choices=["dev", "public"], default="dev")
    p.add_argument("--notes", help="release notes (text) or @file")
    p.add_argument("--api", default=os.environ.get("CLOTH_NEXT_API", DEFAULT_API))
    a = p.parse_args()

    token = os.environ.get("CLOTH_NEXT_RELEASE_TOKEN", "")
    if not token:
        sys.exit("error: set CLOTH_NEXT_RELEASE_TOKEN")
    if not a.file.is_file():
        sys.exit(f"error: no file {a.file}")
    rev = a.source_revision or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    notes = a.notes
    if notes and notes.startswith("@"):
        notes = Path(notes[1:]).read_text(encoding="utf-8").strip()  # server rejects control characters

    size = a.file.stat().st_size
    h = hashlib.sha256()
    with a.file.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    local_sha = h.hexdigest()
    print(f"uploading {a.file.name} ({size / 1e6:.1f} MB, sha256 {local_sha[:16]}...)", file=sys.stderr)

    with a.file.open("rb") as f:
        up = request(a.api, "PUT", f"/v1/release/solver/uploads?filename={urllib.parse.quote(a.file.name)}",
                     token, body=f, length=size, content_type="application/octet-stream")
    if up["sha256"] != local_sha or up["size_bytes"] != size:
        sys.exit(f"error: server received a different file (sha256 {up['sha256']}, {up['size_bytes']} bytes)")

    build = request(a.api, "POST", "/v1/release/solver/builds", token, body=json.dumps({
        "upload_id": up["upload_id"], "version": a.version, "platform": a.platform, "backend": a.backend,
        "min_addon_version": a.min_addon, "max_addon_version": a.max_addon, "source_revision": rev.lower(),
        "release_notes": notes, "audience": a.audience}).encode())
    print(json.dumps(build, indent=2))


if __name__ == "__main__":
    main()
