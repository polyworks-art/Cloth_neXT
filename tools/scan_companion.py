# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject obviously contaminated or invalid companion executables."""
from pathlib import Path
import argparse
import sys

FORBIDDEN=(b"ppf-cts-server",b"ppf-contact-solver",b"C:\\Users\\",b"/home/")
def scan(path: Path):
    data=path.read_bytes()
    if not data.startswith(b"MZ"): raise ValueError("companion is not a Windows PE executable")
    hits=[item.decode("ascii",errors="replace") for item in FORBIDDEN if item.lower() in data.lower()]
    if hits: raise ValueError("forbidden companion content: "+", ".join(hits))
if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("path",type=Path); args=parser.parse_args()
    try: scan(args.path)
    except (OSError,ValueError) as exc: print(exc,file=sys.stderr); raise SystemExit(1)
    print("companion executable scan passed")
