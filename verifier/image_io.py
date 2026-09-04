"""Verifier-owned, fail-open atomic ThreadMark file processing."""

from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
import time
import uuid

SUPPORTED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"})


@dataclass(frozen=True, slots=True)
class AtomicEncodeResult:
    ok: bool
    reason: str = ""


def encode_file_atomic_result(path, encoder, payload_bits, *, verify=None):
    """Encode atomically and return a bounded, path-free diagnostic result."""
    from PIL import Image

    target = Path(path)
    if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return AtomicEncodeResult(False, "unsupported-format")
    temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}{target.suffix}")
    try:
        marked = None
        source = Image.open(target)
        try:
            source.load()
            source_size = source.size
            original_alpha = None
            if "A" in source.getbands():
                alpha = source.getchannel("A")
                try:
                    original_alpha = alpha.tobytes()
                finally:
                    alpha.close()
            info = dict(source.info)
            marked = encoder.encode(source, payload_bits)
            save_kwargs = {}
            for key in ("icc_profile", "exif", "dpi"):
                if key in info:
                    save_kwargs[key] = info[key]
            if target.suffix.lower() in {".jpg", ".jpeg"}:
                converted = marked.convert("RGB")
                if converted is not marked:
                    marked.close()
                marked = converted
                save_kwargs.update(quality=95, subsampling=0)
        finally:
            source.close()
        # Saving a derived TIFF while its source context is alive causes Pillow
        # to retain non-delete-sharing handles on Windows. Publish only after
        # the source has been closed.
        try:
            marked.save(temporary, **save_kwargs)
        finally:
            marked.close()
        check = Image.open(temporary)
        try:
            check.load()
            if check.size != source_size:
                raise ValueError("encoded image dimensions changed")
            if original_alpha is not None:
                alpha = check.getchannel("A")
                try:
                    if alpha.tobytes() != original_alpha:
                        raise ValueError("encoded image alpha changed")
                finally:
                    alpha.close()
            if verify is not None and not verify(check):
                raise ValueError("encoded image failed ThreadMark verification")
        finally:
            check.close()
        # Match the repository's established Windows publication convention:
        # scanners and indexers can briefly deny delete-sharing even after all
        # Pillow handles are closed. Retry only the atomic replace, and keep the
        # total wait short and bounded.
        for attempt in range(6):
            try:
                os.replace(temporary, target)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 5:
                    raise
                time.sleep(0.01 * (2 ** attempt))
        return AtomicEncodeResult(True)
    except Exception as exc:
        message = " ".join(str(exc).replace("\n", " ").split())[:128]
        return AtomicEncodeResult(
            False, f"{type(exc).__name__}: {message}" if message else type(exc).__name__
        )
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def encode_file_atomic(path, encoder, payload_bits, *, verify=None) -> bool:
    """Backward-compatible boolean wrapper for atomic ThreadMark encoding."""
    return encode_file_atomic_result(
        path, encoder, payload_bits, verify=verify
    ).ok
