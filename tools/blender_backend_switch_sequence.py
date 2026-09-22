# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run the five backend certification Bakes in one Blender process."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_solver_generation_probe as probe


def main() -> None:
    values = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gaia-root", type=Path, required=True)
    parser.add_argument("--lumen-root", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=30)
    args = parser.parse_args(values)
    runs = (
        ("gaia-cuda-1", "cuda", args.gaia_root / "target/cuda/release/ppf-cts-server.exe",
         "official-2026-09-21-21-32-win64"),
        ("gaia-cpu", "cpu", args.gaia_root / "target/cpu/release/ppf-cts-server.exe",
         "official-2026-09-21-21-32-win64"),
        ("gaia-cuda-2", "cuda", args.gaia_root / "target/cuda/release/ppf-cts-server.exe",
         "official-2026-09-21-21-32-win64"),
        ("lumen-cuda", "lumen", args.lumen_root / "target/release/ppf-cts-server.exe",
         "official-2026-08-12-15-47-win64"),
        ("gaia-cuda-3", "cuda", args.gaia_root / "target/cuda/release/ppf-cts-server.exe",
         "official-2026-09-21-21-32-win64"),
    )
    for name, backend, solver, installation in runs:
        print(f"CERT_SEQUENCE_START={name}", flush=True)
        sys.argv = [sys.argv[0], "--", "--solver", str(solver),
                    "--backend", backend, "--frames", str(args.frames),
                    "--registry", str(args.registry),
                    "--installation-id", installation,
                    "--output", str(args.output / name)]
        probe.main()
        print(f"CERT_SEQUENCE_PASS={name}", flush=True)


if __name__ == "__main__":
    main()
