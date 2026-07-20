# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the public ``errors/errors.json`` feed from the single source of
truth (:mod:`cloth_next.core.error_codes`).

The Companion fetches this feed to refresh the user-facing "What to do:" line
without a new build (see ``companion/error_guidance.py``). Generating it from
``ERROR_CODES`` guarantees the website and the shipped catalogue never drift.

Usage::

    python tools/build_error_guidance.py --output ../clothnext-gh-pages/errors/errors.json
    python tools/build_error_guidance.py --check     # exit 1 if --output is stale
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from the repo root without installation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloth_next.core.error_codes import ERROR_CODES  # noqa: E402

SCHEMA = 1
SOURCE = "cloth_next/core/error_codes.py"


def build_document() -> dict:
    """Return the exact JSON document served at ``errors/errors.json``."""
    return {
        "schema": SCHEMA,
        "source": SOURCE,
        "errors": [
            {
                "code": info.code,
                "stage": info.stage,
                "cause": info.cause,
                "action": info.action,
            }
            for info in ERROR_CODES.values()
        ],
    }


def render() -> str:
    """Serialise the feed deterministically (matches the committed feed)."""
    return json.dumps(build_document(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        help="Write the feed here (default: print to stdout).")
    parser.add_argument(
        "--check", action="store_true",
        help="Exit non-zero if --output does not already match the source.")
    args = parser.parse_args(argv)

    payload = render()

    if args.check:
        if args.output is None:
            parser.error("--check requires --output")
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != payload:
            print(f"STALE: {args.output} does not match {SOURCE}", file=sys.stderr)
            return 1
        print(f"OK: {args.output} matches {SOURCE}")
        return 0

    if args.output is None:
        sys.stdout.write(payload)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Wrote {len(ERROR_CODES)} codes to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
