"""Safe inspection and staging extraction of the verified solver archive.

The archive is inspected before extraction: absolute or drive-letter paths,
parent traversal, symbolic links, Windows reparse points, and oversized
contents are all rejected. Extraction writes only into a fresh staging
directory below the managed solver root.
"""

from __future__ import annotations

import re
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from cloth_next.ppf.bootstrap import safe_extract_zip

MAX_ARCHIVE_MEMBERS = 200_000
MAX_TOTAL_UNCOMPRESSED = 8 * 1024 ** 3
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def inspect_archive(archive: Path, *,
                    max_members: int = MAX_ARCHIVE_MEMBERS,
                    max_total_uncompressed: int = MAX_TOTAL_UNCOMPRESSED) -> None:
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
    safe_extract_zip(archive, staging)
    return staging
