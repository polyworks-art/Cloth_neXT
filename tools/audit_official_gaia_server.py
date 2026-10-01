"""Isolated official 0.23 TCMD probe; does not enable a production profile.

Run with the official bundle's Python, from its root. No source patches or
test-rig GPU-check bypasses are permitted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
import uuid

import cbor2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cloth_next.materials import DEFAULT_STATIC_SETTINGS, ShellMaterialSettings
from cloth_next.ppf.coordinates import solver_world_matrix
from cloth_next.ppf.process import SolverProcessConfig, SolverProcessManager
from cloth_next.ppf.schema.data import SceneObject, encode_scene
from cloth_next.ppf.schema.params import SimulationSettings, encode_param
from cloth_next.ppf.transport import TransportConfig
from cloth_next.ppf.wire import ServerAddress, send_tcmd, upload_atomic, data_receive
from cloth_next.ppf_run.fixture import vertical_slice_fixture
from frontend import App


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--two-intra-seams", action="store_true")
    parser.add_argument("--central-bridge", action="store_true")
    args = parser.parse_args()
    assert not os.environ.get("PPF_CTS_DATA_ROOT"), "no test-rig overrides"
    root = Path.cwd()
    App.set_backend("cuda")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    manager = SolverProcessManager(SolverProcessConfig(
        executable_path=root / "target/cuda/release/ppf-cts-server.exe",
        working_directory=root, port=port,
        environment=(("PPF_CTS_BUILD_PYTHON", str(root / "python/python.exe")),
                     ("CARGO_TARGET_DIR", str(root / "target/cuda"))),
    ))
    name = "cnx-official-tcmd-" + uuid.uuid4().hex
    address = ServerAddress("127.0.0.1", port)
    transport = TransportConfig(read_timeout=10)
    report = {"project": name, "result": "INCOMPLETE", "statuses": []}

    def status(request=None):
        response = send_tcmd(address, transport, name, request,
                             allow_server_error=True)
        if response != report["statuses"][-1:] and request:
            print(json.dumps(response), flush=True)
        report["statuses"].append(response)
        if response.get("error"):
            raise RuntimeError(json.dumps(response))
        return response

    def wait_for(expected, timeout=300):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = status()
            if response.get("status") in expected:
                return response
            time.sleep(0.25)
        raise TimeoutError(str(report["statuses"][-1]))

    try:
        print("identity", manager.executable_version(), flush=True)
        manager.start()
        deadline = time.monotonic() + 20
        while True:
            try:
                response = status()
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        print("handshake", json.dumps(response), flush=True)
        assert response["protocol_version"] == "0.23"
        cloth, collider = vertical_slice_fixture()
        if args.two_intra_seams:
            vertices, triangles = [], []
            for height in (0.0, 1.0):
                for left in (0.0, 0.4):
                    base = len(vertices)
                    vertices.extend([[left, 0, height], [left+0.2, 0, height],
                                     [left+0.2, 0, height+0.2], [left, 0, height+0.2]])
                    triangles.extend([[base, base+1, base+2], [base, base+2, base+3]])
            from cloth_next.ppf_run.fixture import FixtureMesh
            cloth = FixtureMesh("two-intra", tuple(map(tuple, vertices)),
                                tuple(map(tuple, triangles)), (0, 0, 4))
        data, dh = encode_scene(
            SceneObject(cloth.name, "audit-cloth", cloth.vertices_local,
                        cloth.triangles, solver_world_matrix(cloth.world_matrix),
                        stitch_pairs=((1, 4), (2, 7), (9, 12), (10, 15))
                        if args.two_intra_seams and not args.central_bridge else ()),
            SceneObject(collider.name, "audit-collider", collider.vertices_local,
                        collider.triangles, solver_world_matrix(collider.world_matrix)),
            schema_version=2)
        param, ph = encode_param(
            SimulationSettings(frame_count=32,
                               fps=1000 if args.two_intra_seams else 24,
                               gravity_blender=(0, 0, 0) if args.two_intra_seams else (0, 0, -9.81)), cloth.name, "audit-cloth",
            collider.name, "audit-collider", shell=ShellMaterialSettings(),
            static=DEFAULT_STATIC_SETTINGS, schema_version=2,
            protocol_version="0.22")  # identical stable schema; no fake profile
        if args.two_intra_seams:
            tree = cbor2.loads(param)
            tree["payload"]["group"][0][0]["density"] = 1000.0
            tree["payload"]["scene"]["dt"] = 0.0001
            if args.central_bridge:
                tree["payload"]["cross_stitch"] = [
                    {"source_uuid": "audit-cloth", "target_uuid": "audit-cloth",
                     "ind": [[a, a, a, b, b, b] for a, b in pairs],
                     "w": [[1, 0, 0, 1, 0, 0]]*2,
                     "stitch_stiffness": strength}
                    for pairs, strength in ((((1, 4), (2, 7)), 100),
                                            (((9, 12), (10, 15)), 450))]
            param = cbor2.dumps(tree)
            import hashlib
            ph = hashlib.sha256(param).hexdigest()
        prepared = None
        if args.central_bridge:
            from cloth_next.ppf_run.session import SessionScene
            from cloth_next.ppf.official_scene_bridge import prepare_scene
            prepared = prepare_scene(SessionScene(name, cloth.name, "audit-cloth",
                len(cloth.vertices_local), collider.name, "audit-collider", 32,
                data, param, dh, ph), enabled=True)
            data, param = prepared.data_payload, prepared.param_payload
            dh, ph = prepared.data_hash, prepared.param_hash
        upload_atomic(address, transport, project_name=name, data_payload=data,
                      param_payload=param, data_hash=dh, param_hash=ph)
        status("build")
        ready = wait_for({"READY"})
        print("ready", json.dumps(ready), flush=True)
        # The published multi-backend server stores uploads under target/,
        # while App.recover resolves the frontend's canonical data directory.
        # Use the documented Windows saved-session pointer representation,
        # without monkeypatching the path resolver or private scene state.
        pointer = Path(App.get_data_dirpath()) / "symlinks" / (name + ".txt")
        pointer.parent.mkdir(parents=True, exist_ok=True)
        with pointer.open("x", encoding="utf-8") as stream:
            stream.write(str(Path(ready["root"]) / "session"))
        recovered = App.recover(name)
        report["public_recovery_path"] = recovered.info.path
        print("public recovery", recovered.info.path, flush=True)
        if args.two_intra_seams:
            from audit_official_gaia_stitches import read_rows, separations
            ind, weights, _ = read_rows(recovered)
            expected = np.asarray([100, 100, 450, 450], dtype=np.float32)
            if prepared is not None:
                from cloth_next.ppf.official_scene_worker import apply_bindings
                report["central_bridge"] = apply_bindings(name, ready["root"],
                    json.loads(prepared.official_bridge_json))
            else:
                recovered.fixed_scene.set_stitch(ind, weights, stiffness=expected)
                recovered = recovered.session.build()
            recovered = App.recover(name)
            np.testing.assert_array_equal(read_rows(recovered)[2], expected)
            report["stiffness"] = expected.tolist()
        status("start")
        finished = wait_for({"READY", "RESUMABLE"})
        assert finished["frame"] >= 31
        report["last_status"] = finished
        report["map_bytes"] = len(data_receive(address, transport,
            project_name=name, path="session/map.pickle"))
        if args.two_intra_seams:
            gaps = []
            for frame in range(32):
                vertices, actual_frame = recovered.get.vertex(frame)
                assert actual_frame == frame and np.isfinite(vertices).all()
                gaps.append(separations(vertices, ind))
            gaps = np.asarray(gaps)
            low, high = gaps[:, :2].mean(axis=1), gaps[:, 2:].mean(axis=1)
            assert low[-1] < low[0] and high[-1] < high[0]
            difference = float(np.max(low-high))
            assert difference > 0.005
            report["maximum_low_minus_high"] = difference
            report["gaps"] = gaps.tolist()
        report["result"] = "PASS"
    finally:
        poll = manager.poll()
        report["stdout"] = poll.stdout_tail
        report["stderr"] = poll.stderr_tail
        manager.stop()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
