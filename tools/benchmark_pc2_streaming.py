"""Reproducible synthetic PC2 import benchmark; never imported by the add-on."""
from __future__ import annotations

import argparse
import json
import tempfile
import time
import tracemalloc
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloth_next.bake.pc2 import StreamingPc2Writer, read_header
from cloth_next.ppf.coordinates import transform_points_numpy


CASES = ((10_000, 50), (50_000, 250))


def run_case(vertices: int, frames: int) -> dict:
    output = Path(tempfile.gettempdir()) / f"clothnext-stream-{vertices}-{frames}.pc2"
    base = np.arange(vertices * 3, dtype="<f4").reshape(vertices, 3) / 1000
    matrix = ((1.0, 0.0, 0.0, 0.25), (0.0, 0.0, -1.0, 2.0),
              (0.0, 1.0, 0.0, -1.0), (0.0, 0.0, 0.0, 1.0))
    transform_seconds = write_seconds = 0.0
    tracemalloc.start()
    started = time.perf_counter()
    writer = StreamingPc2Writer(output, vertex_count=vertices,
                                frame_count=frames)
    try:
        for frame in range(frames):
            step = time.perf_counter()
            local = transform_points_numpy(matrix, base + np.float32(frame))
            transform_seconds += time.perf_counter() - step
            step = time.perf_counter()
            writer.write_frame(local)
            write_seconds += time.perf_counter() - step
        step = time.perf_counter()
        writer.finalize()
        finalize_seconds = time.perf_counter() - step
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        size = output.stat().st_size
        read_header(output)
        return {"vertices": vertices, "frames": frames, "pc2_bytes": size,
                "transform_seconds": transform_seconds,
                "write_seconds": write_seconds,
                "finalize_seconds": finalize_seconds,
                "flush_seconds": writer.flush_seconds,
                "fstat_seconds": writer.fstat_seconds,
                "fsync_seconds": writer.fsync_seconds,
                "close_seconds": writer.close_seconds,
                "replace_seconds": writer.replace_seconds,
                "validation_seconds": writer.validation_seconds,
                "elapsed_seconds": elapsed, "peak_python_bytes": peak,
                "throughput_mib_s": size / 1048576 / elapsed}
    finally:
        tracemalloc.stop()
        writer.abort()
        output.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("small", "medium", "all"),
                        default="all")
    args = parser.parse_args()
    selected = CASES if args.case == "all" else (CASES[args.case == "medium"],)
    for case in selected:
        print(json.dumps(run_case(*case), sort_keys=True), flush=True)
    print("Solver performance depends on the selected time step.")


if __name__ == "__main__":
    main()
