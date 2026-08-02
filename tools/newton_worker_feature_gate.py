# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic managed-Newton gate for multi-cloth and deforming colliders."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloth_next.newton_preview.client import NewtonWorkerClient
from cloth_next.newton_preview.contracts import (
    ColliderAnimation, PreviewCloth, PreviewCreateRequest, PreviewMaterial,
    PreviewMesh, PreviewQuality, PreviewResult)
from cloth_next.newton_preview.request_artifact import write_request_artifact


def _wait(client, event, timeout=180.0, predicate=lambda _message: True):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = client.poll(0.1)
        if message is None:
            continue
        if message.get("event") == "error":
            raise RuntimeError(message.get("message", "Newton worker error"))
        if message.get("event") == event and predicate(message):
            return message
    raise TimeoutError(f"Newton worker did not emit {event}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    triangle_a = PreviewMesh(
        ((-0.8, 0.0, 1.0), (-0.2, 0.0, 1.0), (-0.5, 0.6, 1.0)),
        ((0, 1, 2),))
    triangle_b = PreviewMesh(
        ((0.2, 0.0, 1.2), (0.8, 0.0, 1.2), (0.5, 0.6, 1.2)),
        ((0, 1, 2),))
    floor = PreviewMesh(
        ((-2.0, -2.0, 0.0), (2.0, -2.0, 0.0),
         (2.0, 2.0, 0.0), (-2.0, 2.0, 0.0)),
        ((0, 1, 2), (0, 2, 3)))
    material = PreviewMaterial(
        0.2, 1000.0, 300.0, 5.0, 1.0, 1.0, 0.3, 0.01, 0.005)
    moved_floor = tuple((x, y, z + 0.05) for x, y, z in floor.vertices)
    with tempfile.TemporaryDirectory(prefix="clothnext-newton-gate-") as directory:
        request = PreviewCreateRequest(
            "worker-feature-gate", "multi-animated-scene", triangle_a,
            (floor,), (), material,
            PreviewQuality(substeps=2, iterations=2, snapshot_cadence=1),
            1, 2, 24.0, 1.0, (0.0, 0.0, -9.81), directory,
            additional_cloths=(PreviewCloth(
                "second-cloth", triangle_b, (), material),),
            collider_animations=(ColliderAnimation(
                0, (floor.vertices, moved_floor)),))
        request.validate()
        client = NewtonWorkerClient(
            args.python, package_root=args.repo.resolve(), startup_timeout=60.0)
        try:
            health = client.start()
            artifact = write_request_artifact(
                request.result_directory, request.to_wire())
            client.send("create_preview", request_artifact=artifact,
                        result_directory=request.result_directory)
            _wait(client, "created")
            client.send("update_target_frame", frame=2)
            result_message = _wait(
                client, "result", predicate=lambda item: int(item["frame"]) == 2)
            result = PreviewResult(
                str(result_message["session_id"]),
                str(result_message["scene_identity"]),
                int(result_message["frame"]),
                int(result_message["vertex_count"]),
                str(result_message["artifact"]),
                str(result_message["sha256"]),
                bool(result_message.get("complete", False)))
            result.validate_for(request)
            report = {
                "result": "passed", "cloth_objects": 2,
                "animated_colliders": 1, "vertex_count": result.vertex_count,
                "frame": result.frame, "health": health}
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, sort_keys=True))
        finally:
            client.shutdown()


if __name__ == "__main__":
    main()
