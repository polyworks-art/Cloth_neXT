"""Centralized Tk font families; keeps Windows visuals and Linux geometry stable."""

from __future__ import annotations

import sys

UI_FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
UI_SEMIBOLD = "Segoe UI Semibold" if sys.platform == "win32" else "DejaVu Sans"
UI_SYMBOL = "Segoe UI Symbol" if sys.platform == "win32" else "DejaVu Sans"
