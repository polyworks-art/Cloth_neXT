# SPDX-License-Identifier: GPL-3.0-or-later
"""Safe, optional online recovery guidance for Companion error codes."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request


GUIDANCE_URL = (
    "https://polyworks-art.github.io/Cloth_neXT/errors/errors.json"
)
MAX_RESPONSE_BYTES = 128 * 1024
CACHE_SECONDS = 10 * 60
_CODE = re.compile(r"CNX-E\d{3}")


def parse_guidance(payload: bytes) -> dict[str, str]:
    """Validate the small public JSON feed and return code-to-action text."""
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("error guidance response is too large")
    document = json.loads(payload.decode("utf-8"))
    if document.get("schema") != 1 or not isinstance(document.get("errors"), list):
        raise ValueError("unsupported error guidance schema")
    guidance: dict[str, str] = {}
    for item in document["errors"]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip().upper()
        action = " ".join(str(item.get("action", "")).split())
        if _CODE.fullmatch(code) and 1 <= len(action) <= 500:
            guidance[code] = action
    return guidance


def replace_recommendation(details: str, action: str) -> str:
    """Replace only the user-facing recommendation, preserving diagnostics."""
    line = f"What to do: {action}"
    lines = str(details or "").splitlines()
    for index, existing in enumerate(lines):
        if existing.strip().startswith("What to do:"):
            lines[index] = line
            return "\n".join(lines)
    return "\n".join((*lines, line)) if lines else line


class ErrorGuidanceClient:
    """Fetch guidance off the UI thread and retain a short in-memory cache."""

    def __init__(self, *, url: str = GUIDANCE_URL, timeout: float = 2.5):
        self.url = url
        self.timeout = timeout
        self._guidance: dict[str, str] = {}
        self._loaded_at = 0.0
        self._loading = False
        self._lock = threading.Lock()

    def request(self, code: str) -> None:
        code = str(code or "").strip().upper()
        if not _CODE.fullmatch(code):
            return
        with self._lock:
            fresh = time.monotonic() - self._loaded_at < CACHE_SECONDS
            if fresh:
                return
            if self._loading:
                return
            self._loading = True
        threading.Thread(
            target=self._fetch,
            name="clothnext-error-guidance", daemon=True).start()

    def get(self, code: str) -> str:
        """Return currently cached guidance without blocking the UI thread."""
        code = str(code or "").strip().upper()
        with self._lock:
            return self._guidance.get(code, "")

    def _fetch(self) -> None:
        try:
            request = urllib.request.Request(
                self.url,
                headers={"User-Agent": "Cloth-NeXt-Companion/1"})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
            guidance = parse_guidance(payload)
            with self._lock:
                self._guidance = guidance
                self._loaded_at = time.monotonic()
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            # Online guidance is optional; the bundled recommendation remains.
            pass
        finally:
            with self._lock:
                self._loaded_at = time.monotonic()
                self._loading = False
