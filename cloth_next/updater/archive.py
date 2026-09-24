# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Safe inspection and staging extraction of the verified solver archive.

The archive is inspected before extraction: absolute or drive-letter paths,
parent traversal, symbolic links, Windows reparse points, and oversized
contents are all rejected. Extraction writes only into a fresh staging
directory below the managed solver root.
"""

from __future__ import annotations

import re
import posixpath
import stat
import tarfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from ..ppf.bootstrap import safe_extract_zip

MAX_ARCHIVE_MEMBERS = 200_000
MAX_TOTAL_UNCOMPRESSED = 8 * 1024 ** 3
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _tar_link_target(info: tarfile.TarInfo) -> str:
    base = posixpath.dirname(info.name) if info.issym() else ""
    target = posixpath.normpath(posixpath.join(base, info.linkname))
    if (target.startswith("../") or target == ".." or target.startswith("/")
            or _DRIVE_LETTER_RE.match(target)):
        raise ValueError(f"unsafe archive link rejected: {info.name}")
    return target


def inspect_archive(archive: Path, *,
                    max_members: int = MAX_ARCHIVE_MEMBERS,
                    max_total_uncompressed: int = MAX_TOTAL_UNCOMPRESSED) -> None:
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive, "r:*") as bundle:
            members = bundle.getmembers()
            if len(members) > max_members:
                raise ValueError(
                    f"archive has {len(members)} members, limit is {max_members}")
            total = 0
            for info in members:
                name = info.name.replace("\\", "/")
                posix = PurePosixPath(name)
                if (posix.is_absolute() or name.startswith("/")
                        or _DRIVE_LETTER_RE.match(name)):
                    raise ValueError(f"absolute archive path rejected: {info.name}")
                if ".." in posix.parts:
                    raise ValueError(f"path traversal rejected: {info.name}")
                if info.issym() or info.islnk():
                    _tar_link_target(info)
                elif not (info.isfile() or info.isdir()):
                    raise ValueError(f"special archive member rejected: {info.name}")
                total += info.size
                if total > max_total_uncompressed:
                    raise ValueError("archive exceeds the uncompressed size limit of "
                                     f"{max_total_uncompressed} bytes")
        return
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > max_members:
            raise ValueError(f"archive has {len(members)} members, limit is {max_members}")
        total = 0
        for info in members:
            name = info.filename.replace("\\", "/")
            posix = PurePosixPath(name)
            if posix.is_absolute() or name.startswith("/") or _DRIVE_LETTER_RE.match(name):
                raise ValueError(f"absolute archive path rejected: {info.filename}")
            if ".." in posix.parts:
                raise ValueError(f"path traversal rejected: {info.filename}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"symbolic link rejected: {info.filename}")
            if info.external_attr & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f"reparse point rejected: {info.filename}")
            total += info.file_size
            if total > max_total_uncompressed:
                raise ValueError("archive exceeds the uncompressed size limit of "
                                 f"{max_total_uncompressed} bytes")


def extract_to_staging(archive: Path, staging_root: Path) -> Path:
    """Inspect the archive, then extract it into a fresh staging directory."""
    inspect_archive(archive)
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / f"install-{uuid.uuid4().hex}"
    if tarfile.is_tarfile(archive):
        staging.mkdir(parents=False, exist_ok=False)
        with tarfile.open(archive, "r:*") as bundle:
            members = bundle.getmembers()
            by_name = {posixpath.normpath(info.name): info for info in members}
            for info in members:
                if info.issym() or info.islnk():
                    continue
                destination = staging.joinpath(
                    *PurePosixPath(info.name.replace("\\", "/")).parts)
                if info.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(info)
                if source is None:
                    raise ValueError(f"archive member could not be read: {info.name}")
                with source, destination.open("wb") as output:
                    import shutil
                    shutil.copyfileobj(source, output)
                destination.chmod(info.mode & 0o777)
            for info in members:
                if not (info.issym() or info.islnk()):
                    continue
                target_name = _tar_link_target(info)
                target_info = by_name.get(target_name)
                seen = {posixpath.normpath(info.name)}
                while target_info is not None and (
                        target_info.issym() or target_info.islnk()):
                    if target_name in seen:
                        raise ValueError(f"archive link cycle rejected: {info.name}")
                    seen.add(target_name)
                    target_name = _tar_link_target(target_info)
                    target_info = by_name.get(target_name)
                if target_info is None or not target_info.isfile():
                    raise ValueError(f"archive link target is not a regular file: {info.name}")
                destination = staging.joinpath(
                    *PurePosixPath(info.name.replace("\\", "/")).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(target_info)
                if source is None:
                    raise ValueError(f"archive link target could not be read: {info.name}")
                with source, destination.open("wb") as output:
                    import shutil
                    shutil.copyfileobj(source, output)
                destination.chmod(target_info.mode & 0o777)
    else:
        safe_extract_zip(archive, staging)
    return staging
