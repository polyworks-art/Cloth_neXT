# SPDX-License-Identifier: GPL-3.0-or-later
"""Real pinned-Newton worker smoke test; no Blender dependency."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloth_next.newton_preview.client import NewtonWorkerClient
from cloth_next.newton_preview.contracts import (
    PreviewCreateRequest, PreviewMaterial, PreviewMesh, PreviewQuality)


def _wait(client, predicate, timeout=180.0):
    deadline = time.monotonic() + timeout
    messages = []
    while time.monotonic() < deadline:
        message = client.poll(0.1)
        if message is None:
            if client.process.poll() is not None:
                raise RuntimeError(client.failure_details())
            continue
        messages.append(message)
        if message.get("event") == "error":
            raise RuntimeError(
                f"{message.get('message', 'Newton worker error')}\n"
                f"{message.get('traceback', '')}")
        if predicate(message):
            return message, messages
    raise TimeoutError("Newton worker smoke test timed out")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--solver", choices=("VBD", "STYLE3D"), default="VBD")
    args = parser.parse_args()
    root = ROOT
    output = Path(tempfile.mkdtemp(prefix="clothnext-newton-smoke-"))
    client = NewtonWorkerClient(args.python, package_root=root,
                                startup_timeout=60.0)
    started = time.perf_counter()
    try:
        health = client.start()
        mesh = PreviewMesh(
            ((-0.5, 0.0, 1.0), (0.5, 0.0, 1.0),
             (-0.5, 0.0, 0.0), (0.5, 0.0, 0.0)),
            ((0, 2, 1), (1, 2, 3)))
        material = PreviewMaterial(1.0, 1000.0, 700.0, 10.0,
                                   1.0, 1.0, 0.2, 0.002, 0.001)
        request = PreviewCreateRequest(
            "worker-smoke", "worker-smoke-scene", mesh, (), (0, 1),
            material, PreviewQuality(substeps=2, iterations=4,
                                     snapshot_cadence=2,
                                     maximum_snapshots=4,
                                     self_collision=False),
            1, 3, 24.0, 1.0, (0.0, 0.0, -9.81), str(output),
            solver=args.solver)
        client.send("create_preview", request=request.to_wire())
        created, create_messages = _wait(
            client, lambda item: item.get("event") == "created")
        initial, initial_messages = _wait(
            client, lambda item: item.get("event") == "result"
            and item.get("frame") == 1)
        client.send("update_target_frame", frame=3)
        forward, forward_messages = _wait(
            client, lambda item: item.get("event") == "result"
            and item.get("frame") == 3)
        client.send("restore_snapshot", frame=1)
        rewind, rewind_messages = _wait(
            client, lambda item: item.get("event") == "result"
            and item.get("frame") == 1)
        report = {
            "result": "passed", "health": health, "created": created,
            "initial": initial, "forward": forward, "rewind": rewind,
            "worker_pid": client.pid,
            "elapsed_seconds": time.perf_counter() - started,
            "message_count": sum(map(len, (create_messages, initial_messages,
                                           forward_messages, rewind_messages))),
        }
    except Exception as exc:
        report = {"result": "failed", "error": str(exc),
                  "details": client.failure_details()}
        raise
    finally:
        client.shutdown()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
