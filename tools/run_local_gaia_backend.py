# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Serve one GAIA package from the isolated contract test double (local only).

    python tools/run_local_gaia_backend.py --package <gaia zip> --ready <file>
        [--access granted|none] [--auto-approve]

Writes {"url": ...} to --ready once listening. Device-link codes are approved
automatically for one test account when --auto-approve is given. Never talks
to production; nothing here is a real PolyWorks service.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.polyworks_backend import ContractBackend  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--access", choices=("granted", "none"), default="granted")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--seconds", type=float, default=1800)
    args = parser.parse_args()
    backend = ContractBackend().start()
    user = backend.add_user("gaia-tester@example.com")
    if args.access == "granted":
        backend.gaia_access[user] = "granted"
    name = args.package.name.removesuffix(".zip")
    version, rest = name.removeprefix("gaia-").split("+", 1)
    backend_id, platform, rev = rest.rsplit(".", 2)[0], rest.rsplit(".", 2)[1], rest.rsplit(".", 2)[2]
    build_id = backend.publish_gaia_build(args.package.read_bytes(), version=version,
                                          backend=backend_id, platform=platform, rev=rev)
    args.ready.write_text(json.dumps({"url": backend.url, "build_id": build_id}), encoding="utf-8")
    print(json.dumps({"url": backend.url, "build_id": build_id}), flush=True)
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        if args.auto_approve:
            with backend.lock:
                pending = [r["user_code"] for r in backend.device_codes.values() if r["decision"] is None]
            for code in pending:
                backend.approve(code, user)
        time.sleep(0.3)
    backend.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
