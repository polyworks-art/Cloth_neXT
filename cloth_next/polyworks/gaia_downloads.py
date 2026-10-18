# SPDX-License-Identifier: GPL-3.0-or-later
"""GAIA build choice and resumable, verified download (Companion v1 handoff 5-7).

The server is the authority: a build is only downloaded through a fresh
download session, every request is authorised again by the edge, and the
signed URL (its ``t`` parameter is the credential) is never logged, stored or
shown. A partial file sits next to the target under ``.partial`` and is
resumed with ``Range``; the finished file must match size and SHA-256 before
anyone may install it. No ``bpy``.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import http.client
import os
from pathlib import Path
import re
import ssl
import time
from typing import Callable
from urllib.parse import urlsplit

from .api import ApiError, _decode_json, _is_loopback

PLATFORM = "windows-x64"
BACKEND = "cuda12.8"
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 30.0
CHUNK = 1024 * 1024
BACKOFF = (2, 5, 15, 60)
DOWNLOAD_HOSTS = frozenset({"downloads.cloth-next.de"})
DOWNLOAD_HOST_ENV = "CLOTH_NEXT_GAIA_DOWNLOAD_HOST"  # loopback test servers only
# Codes after which the caller must stop or start over (handoff 6).
STOP_CODES = frozenset({"gaia_access_required", "build_unavailable"})
RENEW_CODES = frozenset({"download_expired", "forbidden"})


class DownloadError(Exception):
    def __init__(self, code: str, *, retry_after: float | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class DownloadCancelled(Exception):
    pass


def _version_key(value: str | None) -> tuple:
    if not value:
        return ()
    return tuple(int(part) for part in re.findall(r"\d+", value.split("-")[0].split("+")[0]))


def compatible_builds(builds: list[dict], addon_version: str, *,
                      platform: str = PLATFORM, backend: str = BACKEND) -> list[dict]:
    """Builds for this platform/backend whose add-on range includes this add-on."""
    local = _version_key(addon_version)
    result = []
    for build in builds:
        if build.get("platform") != platform or build.get("backend") != backend:
            continue
        minimum, maximum = build.get("min_addon_version"), build.get("max_addon_version")
        if minimum and local < _version_key(minimum):
            continue
        if maximum and local > _version_key(maximum):
            continue
        result.append(build)
    return result


def choose_build(builds: list[dict], addon_version: str, *, channel: str = "stable") -> dict | None:
    """The current build of one channel, compatible with this add-on and machine."""
    usable = [b for b in compatible_builds(builds, addon_version) if b.get("channel") == channel]
    return max(usable, key=lambda b: (b.get("published_at") or "", b["version"])) if usable else None


def validate_download_url(url: str) -> tuple[str, str, int | None, str]:
    """Only HTTPS to the PolyWorks download host (or a loopback test server)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    allowed_test = os.environ.get(DOWNLOAD_HOST_ENV, "").strip().lower()
    if parts.scheme == "https" and host in DOWNLOAD_HOSTS:
        pass
    elif parts.scheme in ("http", "https") and _is_loopback(host) and host == allowed_test:
        pass
    else:
        raise DownloadError("forbidden")
    if parts.username or parts.password or parts.fragment:
        raise DownloadError("forbidden")
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    return parts.scheme, host, parts.port, target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class DownloadProgress:
    done: int = 0
    total: int = 0


def _request(url: str, offset: int):
    scheme, host, port, target = validate_download_url(url)
    if scheme == "https":
        connection = http.client.HTTPSConnection(host, port, timeout=CONNECT_TIMEOUT,
                                                 context=ssl.create_default_context())
    else:
        connection = http.client.HTTPConnection(host, port, timeout=CONNECT_TIMEOUT)
    headers = {"User-Agent": "ClothNeXt-Blender", "Accept": "application/octet-stream"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    connection.connect()
    connection.sock.settimeout(READ_TIMEOUT)
    connection.request("GET", target, headers=headers)
    return connection, connection.getresponse()


def _error_from(response) -> DownloadError:
    raw = response.read(64 * 1024)
    body = _decode_json(raw)
    code = (body.get("error") or {}).get("code") if isinstance(body, dict) else None
    retry = response.getheader("Retry-After")
    try:
        retry_after = float(retry) if retry is not None else None
    except ValueError:
        retry_after = None
    if response.status == 429:
        return DownloadError("rate_limited", retry_after=retry_after)
    if response.status >= 500:
        return DownloadError("service_unavailable", retry_after=retry_after)
    if isinstance(code, str) and code in STOP_CODES | RENEW_CODES:
        return DownloadError(code)
    return DownloadError("forbidden" if response.status == 403 else "invalid_response")


def download(new_session: Callable[[], dict], target: Path, *,
             progress: Callable[[int, int], None] = lambda _d, _t: None,
             cancelled: Callable[[], bool] = lambda: False,
             sleep: Callable[[float], None] = time.sleep,
             max_sessions: int = 4) -> Path:
    """Download into ``target`` (verified) using sessions from ``new_session``.

    ``new_session()`` returns the download-session response ({url, sha256,
    size_bytes}); it is called again after an expired link. Returns the
    verified file; raises DownloadError / DownloadCancelled otherwise and keeps
    the partial file for a later resume unless it is corrupt or unavailable.
    """
    target = Path(target)
    partial = target.with_name(target.name + ".partial")
    target.parent.mkdir(parents=True, exist_ok=True)
    session = new_session()
    expected_sha, expected_size = session["sha256"], session["size_bytes"]
    sessions, failures = 1, 0
    while True:
        if cancelled():
            raise DownloadCancelled()
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > expected_size:
            partial.unlink()
            offset = 0
        if offset == expected_size:
            break
        connection = None
        try:
            connection, response = _request(session["url"], offset)
            if response.status == 200 and offset:
                offset = 0  # handoff 6.3: no range support, start from zero
                partial.unlink()
            elif response.status not in (200, 206):
                raise _error_from(response)
            mode = "ab" if offset else "wb"
            with partial.open(mode) as stream:
                done = offset
                while True:
                    if cancelled():
                        raise DownloadCancelled()
                    block = response.read(CHUNK)
                    if not block:
                        break
                    done += len(block)
                    if done > expected_size:
                        raise DownloadError("size_mismatch")
                    stream.write(block)
                    progress(done, expected_size)
            failures = 0
        except DownloadError as exc:
            if exc.code in RENEW_CODES and sessions < max_sessions:
                session = new_session()  # fresh authorised link; resume with Range
                if (session["sha256"], session["size_bytes"]) != (expected_sha, expected_size):
                    partial.unlink(missing_ok=True)
                    raise DownloadError("build_changed") from None
                sessions += 1
                continue
            if exc.code in ("rate_limited", "service_unavailable") and failures < len(BACKOFF):
                sleep(exc.retry_after if exc.retry_after is not None else BACKOFF[failures])
                failures += 1
                continue
            if exc.code in STOP_CODES or exc.code == "size_mismatch":
                partial.unlink(missing_ok=True)
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError):
            if failures >= len(BACKOFF):
                raise DownloadError("network_unavailable") from None
            sleep(BACKOFF[failures])
            failures += 1
            continue
        finally:
            if connection is not None:
                connection.close()
    if sha256_file(partial) != expected_sha:
        partial.unlink(missing_ok=True)
        raise DownloadError("checksum_mismatch")
    os.replace(partial, target)
    return target


__all__ = ["ApiError", "DownloadCancelled", "DownloadError", "choose_build", "compatible_builds",
           "download", "validate_download_url"]
