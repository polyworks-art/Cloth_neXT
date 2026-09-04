"""Internal offline ThreadMark encoder worker used by Blender renders."""

from __future__ import annotations

from pathlib import Path
import socket

from cloth_next.provenance.payload import ThreadMarkPayloadV1
from cloth_next.provenance.worker_protocol import receive_message, send_message
from verifier.image_io import encode_file_atomic_result
from verifier.trustmark_backend import TrustMarkOnnxBackend


def _payload_is_valid(image, backend) -> bool:
    signal = backend.decode(image)
    if not signal.ecc_valid or not signal.payload_bits:
        return False
    try:
        ThreadMarkPayloadV1.from_bits(signal.payload_bits)
    except ValueError:
        return False
    return True


def run_worker(*, host: str, port: int, token: str, model_dir: Path) -> int:
    """Load one backend, serve bounded encode requests, then exit."""
    if host != "127.0.0.1" or not 0 < port <= 65535 or len(token) < 32:
        return 2
    try:
        backend = TrustMarkOnnxBackend(
            model_dir, variant="Q", strength=0.80, threads=4
        )
        connection = socket.create_connection((host, port), timeout=10.0)
        connection.settimeout(30.0)
        with connection:
            send_message(connection, {"type": "ready", "token": token})
            while True:
                request = receive_message(connection)
                if request.get("token") != token:
                    send_message(connection, {"ok": False, "reason": "authentication"})
                    return 2
                kind = request.get("type")
                if kind == "shutdown":
                    send_message(connection, {"ok": True})
                    return 0
                if kind != "encode" or not isinstance(request.get("path"), str):
                    send_message(connection, {"ok": False, "reason": "request"})
                    continue
                path = Path(request["path"])
                result = encode_file_atomic_result(
                    path,
                    backend,
                    ThreadMarkPayloadV1().to_bits(),
                    verify=lambda image: _payload_is_valid(image, backend),
                )
                send_message(
                    connection,
                    {"ok": result.ok, "reason": result.reason},
                )
    except (ConnectionError, OSError, RuntimeError, TypeError, ValueError):
        return 2
