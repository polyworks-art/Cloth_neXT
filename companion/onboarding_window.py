# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Premium, non-blocking Splash, Welcome, and What's-New Companion window."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import tkinter as tk
import webbrowser

from companion.ui_fonts import UI_FONT
from cloth_next.onboarding import (CHANGELOG_URL, default_resource_root,
                                   load_welcome, load_whats_new)

BG = "#08090a"
PANEL = "#151719"
PANEL_HOVER = "#202326"
WHITE = "#f7f7f5"
MUTED = "#9b9da1"
LINE = "#303337"


def load_content(mode: str, version: str | None = None,
                 content_root: Path | None = None) -> dict:
    root = content_root or default_resource_root()
    if mode == "welcome":
        return load_welcome(root)
    if mode == "whats-new" and version:
        return load_whats_new(version, root)
    raise ValueError("What's New requires --version MAJOR.MINOR.PATCH")


class InfoWindow:
    WIDTH = 820
    HEIGHT = 500

    def __init__(self, mode: str, content: dict, *, root=None,
                 content_root: Path | None = None, splash_ms: int = 0,
                 version: str | None = None, show_after_updates: bool = True,
                 preference_path: Path | None = None,
                 preference_token: str | None = None):
        self.mode = mode
        self.content = content
        self.version = version or content.get("version", "")
        self.content_root = content_root or default_resource_root()
        self.splash_ms = max(0, splash_ms)
        self.preference_path = preference_path
        self.preference_token = preference_token
        self.root = root or tk.Tk()
        self.root.withdraw()
        self.root.title("Cloth NeXt")
        self.root.configure(bg=BG)
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.root.minsize(self.WIDTH, self.HEIGHT)
        self.root.resizable(False, False)
        self.canvas = tk.Canvas(self.root, width=self.WIDTH, height=self.HEIGHT,
                                bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self._images: list[tk.PhotoImage] = []
        self._after_ids: list[str] = []
        self._show_after_updates = tk.BooleanVar(value=show_after_updates)
        self._load_identity()
        self._load_background()
        if self.splash_ms:
            self._build_splash()
        else:
            self._build_destination()
        self.root.update_idletasks()
        self._center()
        self.root.deiconify()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._schedule(0, self._acknowledge_ready)
        auto_close = os.environ.get("CLOTH_NEXT_COMPANION_AUTO_CLOSE_MS", "")
        if auto_close.isdigit() and int(auto_close) > 0:
            self._schedule(int(auto_close), self.close)

    def _schedule(self, delay: int, callback):
        identifier = self.root.after(delay, callback)
        self._after_ids.append(identifier)
        return identifier

    def _load_identity(self):
        try:
            from companion.app import _asset, _match_windows_title_bar, _windows_identity
            _windows_identity()
            logo = tk.PhotoImage(file=str(_asset("cloth_next.png")))
            self._images.append(logo)
            self.root.iconphoto(True, logo)
            _match_windows_title_bar(self.root, light=False)
        except (ImportError, tk.TclError):
            pass

    def _load_background(self):
        try:
            image = tk.PhotoImage(file=str(
                self.content_root / "assets" / "wireframe-cloth.png"))
            self._images.append(image)
            self._background = image
        except (OSError, tk.TclError):
            self._background = None
        try:
            self._brand_logo = tk.PhotoImage(file=str(
                self.content_root / "assets" / "cloth-next-logo.png"))
            self._brand_logo_large = tk.PhotoImage(file=str(
                self.content_root / "assets" / "cloth-next-logo-splash.png"))
            self._images.extend((self._brand_logo, self._brand_logo_large))
        except (OSError, tk.TclError):
            self._brand_logo = None
            self._brand_logo_large = None

    def _paint_background(self):
        self.canvas.delete("all")
        self.canvas.configure(bg=BG)
        if self._background is not None:
            self.canvas.create_image(self.WIDTH // 2, self.HEIGHT // 2,
                                     image=self._background, anchor="center")

    def _center(self):
        x = max(0, (self.root.winfo_screenwidth() - self.WIDTH) // 2)
        y = max(0, (self.root.winfo_screenheight() - self.HEIGHT) // 2)
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def _text(self, x, y, text, *, size=12, color=WHITE, weight="normal",
              anchor="nw", width=None):
        return self.canvas.create_text(
            x, y, text=text, fill=color, font=(UI_FONT, size, weight),
            anchor=anchor, width=width, justify="left")

    def _brand(self, x=42, y=32, *, large=False):
        scale = 1.35 if large else 1.0
        w = 76 if large else 58
        logo = self._brand_logo_large if large else self._brand_logo
        if logo is not None:
            self.canvas.create_image(x, y, image=logo, anchor="nw")
        self._text(x + w + int(16 * scale), y - int(7 * scale), "Cloth NeXt",
                   size=42 if large else 31, weight="bold")

    def _draw_version(self):
        if self.version:
            self._text(self.WIDTH - 24, self.HEIGHT - 18, f"v{self.version}",
                       size=10, color=MUTED, anchor="se")

    def _build_splash(self):
        self._paint_background()
        self._brand(72, 177, large=True)
        self._text(170, 254, "C L O T H   S I M U L A T I O N   F O R   B L E N D E R",
                   size=8, color=MUTED)
        self._text(42, 414, "Initializing...", size=10, weight="bold")
        self.canvas.create_rectangle(42, 440, 310, 447, fill=LINE, outline="")
        self._progress = self.canvas.create_rectangle(42, 440, 42, 447,
                                                      fill=WHITE, outline="")
        self._draw_version()
        self._splash_started = time.monotonic()
        self._animate_progress()

    def _animate_progress(self):
        elapsed = (time.monotonic() - self._splash_started) * 1000
        fraction = min(1.0, elapsed / max(1, self.splash_ms))
        eased = 1 - (1 - fraction) ** 3
        self.canvas.coords(self._progress, 42, 440, 42 + 268 * eased, 447)
        if fraction < 1:
            self._schedule(32, self._animate_progress)
        else:
            self._fade_to_destination(0)

    def _fade_to_destination(self, step):
        if step < 6:
            self.root.attributes("-alpha", 1.0 - step / 7)
            self._schedule(24, lambda: self._fade_to_destination(step + 1))
            return
        self._build_destination()
        self.root.attributes("-alpha", 0.18)
        self._fade_in(1)

    def _fade_in(self, step):
        self.root.attributes("-alpha", min(1.0, 0.18 + step * 0.14))
        if step < 6:
            self._schedule(24, lambda: self._fade_in(step + 1))

    def _build_destination(self):
        self._paint_background()
        self._brand()
        if self.mode == "welcome":
            self._build_welcome()
        else:
            self._build_whats_new()
        self._draw_version()

    def _build_welcome(self):
        self._text(42, 108, self.content["title"], size=27, weight="bold")
        self._text(44, 150, self.content["subtitle"], size=9, color=MUTED,
                   width=410)
        y = 212
        for index, step in enumerate(self.content["steps"], 1):
            self._text(44, y + 2, f"0{index}", size=8, color=MUTED, weight="bold")
            self._text(82, y, step["title"], size=10, weight="bold")
            self._text(82, y + 20, step["description"], size=7, color=MUTED,
                       width=360)
            y += 64
        self._button(42, 420, 158, 36, self.content["actions"][0]["label"],
                     self.close, primary=True)

    def _release_items(self):
        items = list(self.content["highlights"])
        for key in ("improvements", "fixes"):
            for item in self.content.get(key, ()):
                items.append({"title": item["text"], "description": "",
                              "icon": item["icon"]})
        return items[:5]

    def _build_whats_new(self):
        self._text(42, 106, "What's New", size=29, weight="bold")
        self._text(44, 150, f"V E R S I O N   {self.content['version']}",
                   size=8, color=MUTED, weight="bold")
        self._text(44, 176, self.content["subtitle"], size=9, color=MUTED,
                   width=410)
        y = 215
        for item in self._release_items():
            self.canvas.create_rectangle(44, y + 2, 70, y + 28,
                                         fill=PANEL, outline=LINE)
            try:
                icon = tk.PhotoImage(file=str(self.content_root / item["icon"]))
                self._images.append(icon)
                self.canvas.create_image(57, y + 15, image=icon, anchor="center")
            except (KeyError, OSError, tk.TclError):
                self._text(57, y + 15, "◇", size=11, anchor="center")
            self._text(82, y + 15, item["title"], size=9, weight="bold",
                       width=365, anchor="w")
            self.canvas.create_line(82, y + 29, 450, y + 29, fill=LINE)
            y += 34
        self._button(42, 438, 158, 30, "Continue", self.close, primary=True)
        self._button(212, 438, 178, 30, "View Full Changelog",
                     lambda: webbrowser.open(CHANGELOG_URL, new=2))
        self._checkbox = tk.Checkbutton(
            self.canvas, text="Show this after updates",
            variable=self._show_after_updates, command=self._write_preference,
            bg=BG, fg=MUTED, activebackground=BG, activeforeground=WHITE,
            selectcolor=PANEL, font=(UI_FONT, 9), borderwidth=0,
            highlightthickness=0, cursor="hand2")
        self.canvas.create_window(42, 486, window=self._checkbox, anchor="w")

    def _button(self, x, y, width, height, label, command, *, primary=False):
        fill = WHITE if primary else PANEL
        color = BG if primary else WHITE
        rectangle = self.canvas.create_rectangle(
            x, y, x + width, y + height, fill=fill,
            outline=WHITE if primary else LINE, width=1)
        text = self._text(x + width // 2, y + height // 2, label, size=10,
                          color=color, weight="bold", anchor="center")
        for item in (rectangle, text):
            self.canvas.tag_bind(item, "<Button-1>", lambda _event: command())
            self.canvas.tag_bind(item, "<Enter>", lambda _event, r=rectangle:
                                 self.canvas.itemconfigure(r, fill=(
                                     "#dededb" if primary else PANEL_HOVER)))
            self.canvas.tag_bind(item, "<Leave>", lambda _event, r=rectangle:
                                 self.canvas.itemconfigure(r, fill=fill))

    def _write_preference(self):
        preference_path = getattr(self, "preference_path", None)
        preference_token = getattr(self, "preference_token", None)
        if not preference_path or not preference_token:
            return
        payload = {"token": preference_token,
                   "show_after_updates": self._show_after_updates.get()}
        try:
            preference_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _acknowledge_ready(self):
        path = os.environ.get("CLOTH_NEXT_INFO_READY_PATH")
        token = os.environ.get("CLOTH_NEXT_INFO_READY_TOKEN")
        if path and token:
            try:
                Path(path).write_text(token, encoding="utf-8")
            except OSError:
                pass

    def close(self):
        self._write_preference()
        for identifier in getattr(self, "_after_ids", ()):
            try:
                self.root.after_cancel(identifier)
            except tk.TclError:
                pass
        if hasattr(self, "_after_ids"):
            self._after_ids.clear()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def run_info_window(mode: str, version: str | None = None,
                    content_root: Path | None = None, *, splash_ms: int = 0,
                    show_after_updates: bool = True,
                    preference_path: Path | None = None,
                    preference_token: str | None = None) -> None:
    root = content_root or default_resource_root()
    InfoWindow(mode, load_content(mode, version, root), content_root=root,
               splash_ms=splash_ms, version=version,
               show_after_updates=show_after_updates,
               preference_path=preference_path,
               preference_token=preference_token).run()
