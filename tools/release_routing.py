"""Resolve publication destinations without storing the configured URL in source."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloth_next.updater.addon_versions import parse_version
from cloth_next.updater.channel_policy import publication_targets, release_visible_in

REPOSITORY_SECRET = "CLOTH_NEXT_RELEASE_REPOSITORY_URL"
BRIDGE_VERSION = "2.6.0"


def uses_unified_repository(version: str) -> bool:
    return parse_version(version) > parse_version(BRIDGE_VERSION)


def configured_directory(url: str | None = None) -> str:
    value = os.environ.get(REPOSITORY_SECRET, "") if url is None else url
    if not value:
        raise ValueError(f"{REPOSITORY_SECRET} must be configured for releases after 2.6.0")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "polyworks-art.github.io"
            or parsed.query or parsed.fragment):
        raise ValueError("Configured release repository must be an HTTPS Pages index without query or fragment")
    parts = parsed.path.split("/")
    if (len(parts) != 4 or parts[:2] != ["", "Cloth_neXT"] or parts[3] != "index.json"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", parts[2])
            or parts[2].lower() in {"stable", "beta", "dev", "artifacts", "errors", "superhive"}):
        raise ValueError("Configured release repository must name one dedicated Pages directory")
    return parts[2]


def publication_directories(version: str) -> tuple[str, ...]:
    if uses_unified_repository(version):
        return (configured_directory(),)
    return publication_targets(parse_version(version).channel_name)


def repair_directory(version: str, channel: str) -> str:
    if uses_unified_repository(version):
        if channel != "release":
            raise ValueError("Releases after 2.6.0 can only repair the configured release repository")
        return configured_directory()
    if channel == "release" or not release_visible_in(parse_version(version).channel_name, channel):
        raise ValueError("Release is not eligible for the requested legacy repository")
    if channel == "beta" and version != BRIDGE_VERSION:
        raise ValueError("GitHub Beta is frozen at 2.6.0")
    return channel


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--github-env", action="store_true")
    parser.add_argument("--repair-channel", choices=("stable", "beta", "dev", "release"))
    args = parser.parse_args()
    try:
        targets = ((repair_directory(args.version, args.repair_channel),)
                   if args.repair_channel else publication_directories(args.version))
        if args.github_env:
            if uses_unified_repository(args.version):
                for target in targets:
                    print(f"::add-mask::{target}")
            key = "REPAIR_REPOSITORY_DIRECTORY" if args.repair_channel else "CLOTH_NEXT_PUBLICATION_TARGETS"
            with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as output:
                output.write(f"{key}={' '.join(targets)}\n")
        else:
            print(" ".join(targets))
    except (ValueError, KeyError) as exc:
        parser.exit(1, f"Release routing rejected: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
