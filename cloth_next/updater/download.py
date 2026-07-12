"""HTTPS-only download of the verified official solver asset.

Only the manifest-pinned immutable official URL is ever fetched, redirects are
restricted to expected official GitHub hosts, the size is bounded, progress is
reported, cancellation is honored, and the SHA-256 is verified before anything
is extracted or executed. All of this runs off the Blender main thread; nothing
here imports ``bpy``.
"""

from __future__ import annotations

import threading
import urllib.request
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit

from cloth_next.ppf.bootstrap import sha256_file
from .solver_manifest import SolverCompatibilityEntry

ALLOWED_DOWNLOAD_HOSTS = frozenset({
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})
DEFAULT_MAX_DOWNLOAD_SIZE = 2 * 1024 ** 3
_CHUNK_SIZE = 1024 * 1024

ProgressCallback = Callable[[int, int], None]


class DownloadCancelled(Exception):
    """The user cancelled the download; the partial file is removed."""


class ResponseLike(Protocol):
    def read(self, size: int) -> bytes: ...
    def getheader(self, name: str) -> str | None: ...


def validate_download_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValueError(f"solver downloads require https, got {url!r}")
    if parts.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise ValueError(f"host {parts.hostname!r} is not an expected official "
                         "GitHub download host")


class _StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        validate_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_official_download(url: str, timeout: float = 60.0) -> ResponseLike:
    validate_download_url(url)
    opener = urllib.request.build_opener(_StrictRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "ClothNeXt-Installer"})
    return opener.open(request, timeout=timeout)


def stream_to_file(response: ResponseLike, destination: Path, *,
                   expected_size: int,
                   max_size: int = DEFAULT_MAX_DOWNLOAD_SIZE,
                   progress: ProgressCallback | None = None,
                   cancel: threading.Event | None = None) -> int:
    declared = response.getheader("Content-Length")
    if declared is not None:
        declared_size = int(declared)
        if declared_size > max_size:
            raise ValueError(f"declared download size {declared_size} exceeds the "
                             f"limit of {max_size} bytes")
        if expected_size > 0 and declared_size != expected_size:
            raise ValueError(f"declared download size {declared_size} does not match "
                             f"the manifest download_size {expected_size}")
    total = 0
    with destination.open("wb") as output:
        while True:
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled("solver download cancelled by the user")
            chunk = response.read(_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                raise ValueError(f"download exceeded the limit of {max_size} bytes")
            output.write(chunk)
            if progress is not None:
                progress(total, expected_size)
    if expected_size > 0 and total != expected_size:
        raise ValueError(f"downloaded {total} bytes, manifest expects {expected_size}")
    return total


def verify_sha256(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if actual != expected.lower():
        raise ValueError(f"SHA-256 mismatch for {path.name}: expected {expected}, "
                         f"got {actual}")


def download_asset(entry: SolverCompatibilityEntry, destination: Path, *,
                   open_url: Callable[[str], ResponseLike] = open_official_download,
                   progress: ProgressCallback | None = None,
                   cancel: threading.Event | None = None) -> Path:
    """Download the manifest-pinned asset to ``destination`` (atomic rename)."""
    validate_download_url(entry.official_asset_url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    try:
        response = open_url(entry.official_asset_url)
        stream_to_file(response, partial, expected_size=entry.download_size,
                       progress=progress, cancel=cancel)
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return destination
