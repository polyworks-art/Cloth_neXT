#!/usr/bin/env python3
"""Exercise the Linux Bake title-bar close gate in a real Tk session."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.bake.status import BakeSnapshot, BakeState  # noqa: E402
from cloth_next.bake.transport import InMemoryTransport  # noqa: E402
from companion.app import BakeWindow  # noqa: E402


def main() -> int:
    if not sys.platform.startswith("linux"):
        raise SystemExit("Linux-only verification")

    window = BakeWindow(InMemoryTransport())
    active = BakeSnapshot(
        state=BakeState.SIMULATING,
        progress_current=12,
        progress_total=100,
        status_title="Simulating",
        activity_label="Frame 12",
        can_cancel=True,
    )
    window.show(active)
    window.root.update()
    cancel_right = (window.cancel.winfo_rootx() + window.cancel.winfo_width()
                    - window.root.winfo_rootx())
    assert cancel_right <= window.root.winfo_width(), (
        f"Cancel clipped at {cancel_right}px in {window.root.winfo_width()}px window")
    assert window.cancel.winfo_viewable()
    assert not window._linux_close_enabled
    assert window._linux_close.cget("fg") == "#5f6368"

    window._linux_close.event_generate("<Button-1>")
    window.root.update()
    assert window.root.winfo_exists()
    assert not window._closed

    window.show(BakeSnapshot(state=BakeState.FINISHED))
    window.root.update()
    assert window._linux_close_enabled
    window._linux_close.event_generate("<Button-1>")
    assert window._closed
    print("Linux Bake close gate: active blocked, terminal enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
