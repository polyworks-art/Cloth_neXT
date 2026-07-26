"""Benchmark atomic file uploads against a real local Cloth NeXt solver."""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloth_next.ppf import wire
from cloth_next.ppf.transport import TransportConfig


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def payload(path: Path, size: int):
    block = bytes(range(256)) * 4096
    digest = hashlib.sha256()
    with path.open("wb") as stream:
        remaining = size
        while remaining:
            chunk = block[:min(len(block), remaining)]
            stream.write(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", required=True, type=Path)
    parser.add_argument("--size-mib", type=int, default=100)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    port = free_port()
    process = subprocess.Popen(
        [str(args.solver), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    address = wire.ServerAddress("127.0.0.1", port)
    config = TransportConfig(connect_timeout=2.0, read_timeout=120.0)
    try:
        for _attempt in range(100):
            try:
                with socket.create_connection(
                        (address.host, address.port), timeout=0.5):
                    pass
                break
            except Exception:
                time.sleep(0.05)
        else:
            raise RuntimeError("solver did not become ready")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            digest = payload(path, args.size_mib * 1024 * 1024)
            param = b"x"
            param_hash = hashlib.sha256(param).hexdigest()
            methods = (
                ("chunk-1m", 1 * 1024 * 1024, False),
                ("chunk-4m", 4 * 1024 * 1024, False),
                ("chunk-8m", 8 * 1024 * 1024, False),
                ("sendfile", 4 * 1024 * 1024, True),
            )
            for label, chunk, sendfile in methods:
                samples = []
                for run in range(args.runs + 1):
                    project = f"clothnext_{uuid.uuid4().hex[:16]}"
                    started = time.perf_counter()
                    wire.upload_atomic(
                        address, config, project_name=project,
                        data_payload=path, param_payload=param,
                        data_hash=digest, param_hash=param_hash,
                        file_chunk_size=chunk, use_sendfile=sendfile)
                    elapsed = time.perf_counter() - started
                    try:
                        wire.send_tcmd(address, config, project, "delete")
                    except Exception:
                        pass
                    if run:
                        samples.append(elapsed)
                print(json.dumps({
                    "method": label, "size_mib": args.size_mib,
                    "runs": samples, "minimum": min(samples),
                    "median": median(samples), "maximum": max(samples),
                }, sort_keys=True), flush=True)
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
