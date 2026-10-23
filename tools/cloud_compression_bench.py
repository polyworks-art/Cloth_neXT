# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure transport compression for Cloud package files and raw results (E21).

    <blender>/5.2/python/bin/python.exe tools/cloud_compression_bench.py \
        <phase0 run dir> [<phase0 run dir> ...] --output <report.json>

Run with Blender's Python: it ships ``zstandard`` (Blender uses it for
.blend compression), so no new dependency would be needed. For each package
role and for the downloaded frame blocks it records ratio, streaming
compression / decompression throughput, peak Python memory of the streaming
codec and the net transfer time (compress + send + decompress) against
sending uncompressed at 20/50/200 Mbit/s.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import tracemalloc
import zlib

try:
    import zstandard
except ImportError:  # pragma: no cover - plain CPython without the wheel
    zstandard = None

CHUNK = 4 * 1024 * 1024
BANDWIDTHS = (20, 50, 200)


def files_of(run: Path) -> dict[str, list[Path]]:
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    blobs = run / "account" / "cloud-packages" / "blobs"
    groups: dict[str, list[Path]] = {}
    for item in manifest["files"]:
        role = "hydro-volume" if item["role"] == "hydro-volume" else item["role"]
        groups.setdefault(role, []).append(blobs / item["sha256"][:2] / item["sha256"])
    results = run / "account" / "cloud-results" / manifest["client_job_id"]
    groups["raw-frames"] = sorted(results.glob("frames-*.bin"))
    return groups


def codecs():
    for level in (1, 6, 9):
        yield f"zlib-{level}", (lambda level=level: zlib.compressobj(level)), zlib.decompressobj
    if zstandard is not None:
        for level in (1, 3, 9, 19):
            yield (f"zstd-{level}",
                   lambda level=level: zstandard.ZstdCompressor(level=level).compressobj(),
                   lambda: zstandard.ZstdDecompressor().decompressobj())


def measure(paths: list[Path], make_compressor, make_decompressor) -> dict:
    raw = compressed = 0
    tracemalloc.start()
    compress_seconds = decompress_seconds = 0.0
    for path in paths:
        compressor = make_compressor()
        decompressor = make_decompressor()
        with path.open("rb") as stream:
            while True:
                block = stream.read(CHUNK)
                started = time.perf_counter()
                packed = compressor.compress(block) if block else compressor.flush()
                compress_seconds += time.perf_counter() - started
                compressed += len(packed)
                raw += len(block)
                started = time.perf_counter()
                if packed:
                    decompressor.decompress(packed)
                decompress_seconds += time.perf_counter() - started
                if not block:
                    break
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result = {"raw_bytes": raw, "compressed_bytes": compressed,
              "ratio": round(raw / compressed, 3) if compressed else None,
              "compress_mb_s": round(raw / 1e6 / compress_seconds, 1) if compress_seconds else None,
              "decompress_mb_s": round(raw / 1e6 / decompress_seconds, 1) if decompress_seconds else None,
              "peak_python_mib": round(peak / 2 ** 20, 1), "net_seconds": {}}
    for mbit in BANDWIDTHS:
        plain = raw * 8 / (mbit * 1e6)
        packed = compress_seconds + compressed * 8 / (mbit * 1e6) + decompress_seconds
        result["net_seconds"][f"{mbit}mbit"] = {
            "uncompressed": round(plain, 2), "compressed": round(packed, 2),
            "saving_percent": round(100 * (plain - packed) / plain, 1) if plain else 0.0}
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"zstandard": getattr(zstandard, "__version__", None), "runs": {}}
    for run in args.runs:
        entry = {}
        for role, paths in files_of(run).items():
            if not paths:
                continue
            entry[role] = {name: measure(paths, make, unmake)
                           for name, make, unmake in codecs()}
        report["runs"][run.name] = entry
        print(run.name, "done", flush=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
