# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the public ``errors/errors.json`` feed from the single source of
truth (:mod:`cloth_next.core.error_codes`).

The Companion fetches this feed to refresh the user-facing "What to do:" line
without a new build (see ``companion/error_guidance.py``). Generating it from
``ERROR_CODES`` guarantees the website and the shipped catalogue never drift.

Usage::

    python tools/build_error_guidance.py --output ../clothnext-gh-pages/errors/errors.json
    python tools/build_error_guidance.py --markdown-output docs/ERROR_CODES.md
    python tools/build_error_guidance.py --check --output errors.json
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

_MARKDOWN_INTRO = """# Cloth NeXt Bake error codes

This page is generated from the runtime registry in
`cloth_next/core/error_codes.py`, the canonical source for public Cloth NeXt
error identifiers. Existing identifiers remain stable; the broad `x00` code
in each stage is retained for failures whose specific cause cannot be proven.

The Bake Companion shows the code with concise recovery guidance. It may
request updated action text from
`https://polyworks-art.github.io/Cloth_neXT/errors/errors.json`; only the code
selects an entry. Scene data, filenames, and diagnostic logs are not uploaded.
If the request fails, the guidance bundled with the installed build remains.

| Code | Stage | Cause | First action |
| --- | --- | --- | --- |
"""

_MARKDOWN_DIAGNOSTICS = """

## Diagnostic locations and persistence

- Full Bake failures: Blender configuration folder →
  `cloth_next/logs/bake-errors.log`. This rotating JSON-lines file contains
  the code, job ID, state, activity, stage, summary, and detailed cause.
- Companion lifecycle: the same folder → `companion-startup.log`.
- Per-run solver/worker diagnostics: the `Diagnostic log:` location in the
  Blender error details. The run-local `failure.log` is written atomically.
- Cache metadata remains partial or failed after an unsuccessful Bake. A
  previously complete cache remains authoritative until its replacement is
  fully written and validated.

When reporting a problem, include the exact code, its `bake-errors.log` entry,
and the per-run `failure.log` when available. These files stay local; Cloth
NeXt does not automatically submit them or require a PolyWorks account.
"""


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


def render_markdown() -> str:
    """Render the complete public Markdown catalogue from the registry."""
    lines = [_MARKDOWN_INTRO.rstrip()]
    for info in ERROR_CODES.values():
        values = tuple(str(value).replace("|", r"\|") for value in (
            info.code, info.stage, info.cause, info.action))
        lines.append(f"| `{values[0]}` | {values[1]} | {values[2]} | "
                     f"{values[3]} |")
    lines.append("")
    lines.append(_MARKDOWN_DIAGNOSTICS.strip())
    return "\n".join(lines) + "\n"


def _check(path: Path, expected: str) -> bool:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if current != expected:
        print(f"STALE: {path} does not match {SOURCE}", file=sys.stderr)
        return False
    print(f"OK: {path} matches {SOURCE}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        help="Write the feed here (default: print to stdout).")
    parser.add_argument(
        "--markdown-output", type=Path,
        help="Write the generated Markdown catalogue here.")
    parser.add_argument(
        "--check", action="store_true",
        help="Exit non-zero if --output does not already match the source.")
    args = parser.parse_args(argv)

    payload = render()
    markdown = render_markdown()

    if args.check:
        if args.output is None and args.markdown_output is None:
            parser.error("--check requires --output and/or --markdown-output")
        valid = True
        if args.output is not None:
            valid &= _check(args.output, payload)
        if args.markdown_output is not None:
            valid &= _check(args.markdown_output, markdown)
        return 0 if valid else 1

    if args.output is None and args.markdown_output is None:
        sys.stdout.write(payload)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Wrote {len(ERROR_CODES)} codes to {args.output}")
    if args.markdown_output is not None:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(markdown, encoding="utf-8")
        print(f"Wrote {len(ERROR_CODES)} codes to {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
