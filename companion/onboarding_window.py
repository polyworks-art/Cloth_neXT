# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Premium, non-blocking Splash, Welcome, and What's-New Companion window."""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import sys
import time
import tkinter as tk
import webbrowser

from cloth_next.onboarding import (CHANGELOG_URL, default_resource_root,
                                   load_welcome, load_whats_new)

ONBOARDING_FONT = "Roboto"
_REGISTERED_FONT_ROOTS: set[Path] = set()

BG = "#08090a"
PANEL = "#151719"
PANEL_HOVER = "#202326"
WHITE = "#f7f7f5"
MUTED = "#9b9da1"
LINE = "#303337"


def _register_onboarding_fonts(content_root: Path) -> None:
    """Expose bundled fonts privately before Tk resolves any font families."""
    font_root = (content_root / "fonts").resolve()
    if font_root in _REGISTERED_FONT_ROOTS:
        return
    fonts = tuple(font_root.glob("Roboto-*.ttf"))
    if not fonts:
        return
    try:
        if sys.platform == "win32":
            for font in fonts:
                ctypes.windll.gdi32.AddFontResourceExW(str(font), 0x10, 0)
        elif sys.platform.startswith("linux"):
            library = ctypes.CDLL(ctypes.util.find_library("fontconfig")
                                  or "libfontconfig.so.1")
            library.FcConfigGetCurrent.restype = ctypes.c_void_p
            library.FcConfigAppFontAddFile.argtypes = (
                ctypes.c_void_p, ctypes.c_char_p)
            library.FcConfigAppFontAddFile.restype = ctypes.c_int
            library.FcConfigBuildFonts.argtypes = (ctypes.c_void_p,)
            config = library.FcConfigGetCurrent()
            for font in fonts:
                library.FcConfigAppFontAddFile(config, os.fsencode(font))
            library.FcConfigBuildFonts(config)
    except (AttributeError, OSError):
        return
    _REGISTERED_FONT_ROOTS.add(font_root)


def load_content(mode: str, version: str | None = None,
                 content_root: Path | None = None) -> dict:
    root = content_root or default_resource_root()
    if mode == "welcome":
        return load_welcome(root)
    if mode == "whats-new" and version:
        return load_whats_new(version, root)
    raise ValueError("What's New requires --version MAJOR.MINOR.PATCH")


class InfoWindow:
    SCALE = 0.8
    DESIGN_WIDTH = 820
    DESIGN_HEIGHT = 500
    WIDTH = round(DESIGN_WIDTH * SCALE)
    HEIGHT = round(DESIGN_HEIGHT * SCALE)
    LINUX_TITLEBAR_HEIGHT = 30

    def __init__(self, mode: str, content: dict, *, root=None,
                 content_root: Path | None = None, splash_ms: int = 0,
                 version: str | None = None, show_after_updates: bool = True,
                 preference_path: Path | None = None,
                 preference_token: str | None = None):
        self.mode = mode
        self.content = content
        self.version = version or content.get("version", "")
        self.content_root = content_root or default_resource_root()
        _register_onboarding_fonts(self.content_root)
        self.splash_ms = max(0, splash_ms)
        self.preference_path = preference_path
        self.preference_token = preference_token
        self.root = root or tk.Tk()
        self.root.withdraw()
        self.root.title("Cloth NeXt")
        self.root.configure(bg=BG)
        self._custom_titlebar = sys.platform.startswith("linux")
        self._window_height = self.HEIGHT + (
            self.LINUX_TITLEBAR_HEIGHT if self._custom_titlebar else 0)
        if self._custom_titlebar:
            self.root.overrideredirect(True)
        self.root.geometry(f"{self.WIDTH}x{self._window_height}")
        self.root.minsize(self.WIDTH, self._window_height)
        self.root.resizable(False, False)
        self._images: list[tk.PhotoImage] = []
        if self._custom_titlebar:
            self._build_linux_titlebar()
        self.canvas = tk.Canvas(self.root, width=self.WIDTH, height=self.HEIGHT,
                                bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
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

    def _build_linux_titlebar(self):
        bar = tk.Frame(self.root, height=self.LINUX_TITLEBAR_HEIGHT,
                       bg="#000000", highlightthickness=0)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        left = tk.Frame(bar, bg="#000000")
        left.pack(side="left", fill="y")
        try:
            icon = tk.PhotoImage(file=str(
                self.content_root / "assets" / "cloth-next-logo-ui-crisp.png"))
            icon = icon.subsample(3, 3)
            self._images.append(icon)
            icon_label = tk.Label(left, image=icon, bg="#000000",
                                  borderwidth=0)
            icon_label.pack(side="left", padx=(8, 3))
        except (OSError, tk.TclError):
            icon_label = None
        title = tk.Label(left, text="Cloth NeXt", bg="#000000", fg=WHITE,
                         font=(ONBOARDING_FONT, 9), borderwidth=0)
        title.pack(side="left", padx=(3, 0))

        controls = tk.Frame(bar, bg="#000000")
        controls.pack(side="right", fill="y")
        minimize = tk.Label(controls, text="—", bg="#000000", fg=WHITE,
                            width=5, font=(ONBOARDING_FONT, 10))
        maximize = tk.Label(controls, text="□", bg="#000000", fg="#6d7176",
                            width=5, font=(ONBOARDING_FONT, 10))
        close = tk.Label(controls, text="×", bg="#000000", fg=WHITE,
                         width=5, font=(ONBOARDING_FONT, 12))
        for control in (minimize, maximize, close):
            control.pack(side="left", fill="y")
        minimize.bind("<Button-1>", lambda _event: self._minimize_linux())
        close.bind("<Button-1>", lambda _event: self.close())
        close.bind("<Enter>", lambda _event: close.configure(bg="#c42b1c"))
        close.bind("<Leave>", lambda _event: close.configure(bg="#000000"))

        for widget in (bar, left, title, icon_label):
            if widget is not None:
                widget.bind("<ButtonPress-1>", self._start_window_drag)
                widget.bind("<B1-Motion>", self._drag_window)

    def _start_window_drag(self, event):
        self._drag_origin = (event.x_root - self.root.winfo_x(),
                             event.y_root - self.root.winfo_y())

    def _drag_window(self, event):
        offset_x, offset_y = self._drag_origin
        self.root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _minimize_linux(self):
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.bind("<Map>", self._restore_linux_titlebar, add="+")

    def _restore_linux_titlebar(self, _event=None):
        self.root.after_idle(lambda: self.root.overrideredirect(True))

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
            _match_windows_title_bar(
                self.root, light=False, background_color=0x00000000)
        except (ImportError, tk.TclError):
            pass

    def _load_background(self):
        try:
            self._splash_background = tk.PhotoImage(file=str(
                self.content_root / "assets" / "splash-background.png"))
            self._images.append(self._splash_background)
        except (OSError, tk.TclError):
            self._splash_background = None
        try:
            self._background = tk.PhotoImage(file=str(
                self.content_root / "assets" / "destination-background.png"))
            self._images.append(self._background)
        except (OSError, tk.TclError):
            self._background = None
        try:
            self._brand_logo = tk.PhotoImage(file=str(
                self.content_root / "assets" / "cloth-next-logo-ui-crisp.png"))
            self._brand_logo_large = tk.PhotoImage(file=str(
                self.content_root / "assets" / "cloth-next-logo-splash-crisp.png"))
            self._images.extend((self._brand_logo, self._brand_logo_large))
        except (OSError, tk.TclError):
            self._brand_logo = None
            self._brand_logo_large = None

    def _paint_background(self):
        self.canvas.delete("all")
        self.canvas.configure(bg=BG)
        if self.splash_ms and self._splash_background is not None:
            self.canvas.create_image(0, 0, image=self._splash_background,
                                     anchor="nw")
        elif self._background is not None:
            self.canvas.create_image(0, 0, image=self._background, anchor="nw")

    def _center(self):
        x = max(0, (self.root.winfo_screenwidth() - self.WIDTH) // 2)
        y = max(0, (self.root.winfo_screenheight() - self._window_height) // 2)
        self.root.geometry(f"{self.WIDTH}x{self._window_height}+{x}+{y}")

    def _text(self, x, y, text, *, size=12, color=WHITE, weight="normal",
              anchor="nw", width=None):
        return self.canvas.create_text(
            self._p(x), self._p(y), text=text, fill=color,
            font=(ONBOARDING_FONT, max(6, round(size * self.SCALE)), weight),
            anchor=anchor, width=self._p(width) if width is not None else None,
            justify="left")

    def _p(self, value):
        return round(value * self.SCALE)

    def _brand(self, x=42, y=32, *, large=False):
        logo = self._brand_logo_large if large else self._brand_logo
        if logo is not None:
            self.canvas.create_image(self._p(x), self._p(y), image=logo,
                                     anchor="nw")
        if large:
            self._text(x, y + 102, "Cloth NeXt", size=42, weight="bold")

    def _draw_version(self):
        if self.version:
            self._text(self.DESIGN_WIDTH - 24, self.DESIGN_HEIGHT - 18,
                       f"v{self.version}",
                       size=10, color=MUTED, anchor="se")

    def _build_splash(self):
        self._paint_background()
        self._brand(72, 112, large=True)
        self._text(72, 292, "C L O T H   S I M U L A T I O N   F O R   B L E N D E R",
                   size=8, color=MUTED)
        self._text(72, 414, "Initializing...", size=10, weight="bold")
        progress_y = self._p(443.5)
        progress_width = self._p(7)
        self.canvas.create_line(self._p(72), progress_y, self._p(364), progress_y,
                                fill=LINE, width=progress_width,
                                capstyle=tk.ROUND)
        self._progress = self.canvas.create_line(
            self._p(72), progress_y, self._p(72), progress_y,
            fill=WHITE, width=progress_width, capstyle=tk.ROUND)
        self._draw_version()
        self._splash_started = time.monotonic()
        self._animate_progress()

    def _animate_progress(self):
        elapsed = (time.monotonic() - self._splash_started) * 1000
        fraction = min(1.0, elapsed / max(1, self.splash_ms))
        eased = 1 - (1 - fraction) ** 3
        progress_y = self._p(443.5)
        self.canvas.coords(self._progress, self._p(72), progress_y,
                           self._p(72 + 292 * eased), progress_y)
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
        if self.mode == "welcome":
            self._brand()
            self._build_welcome()
        else:
            self._build_whats_new()
        self._draw_version()

    def _build_welcome(self):
        self._text(112, 39, self.content["title"], size=27, weight="bold")
        y = 160
        for index, step in enumerate(self.content["steps"], 1):
            self._text(48, y + 2, f"0{index}", size=8, color=MUTED, weight="bold")
            self._text(88, y, step["title"], size=10, weight="bold")
            self._text(88, y + 20, step["description"], size=8, color=MUTED,
                       width=315)
            y += 72
        self._button(48, 420, 158, 36, self.content["actions"][0]["label"],
                     self.close, primary=True)

    def _release_items(self):
        items = list(self.content["highlights"])
        for key in ("improvements", "fixes"):
            for item in self.content.get(key, ()):
                items.append({"title": item["text"], "description": "",
                              "icon": item["icon"]})
        return items[:3]

    def _build_whats_new(self):
        self._text(48, 54, "What's New", size=30, weight="bold")
        self._text(48, 102, self.content["subtitle"], size=10, color=MUTED,
                   width=390)
        y = 145
        for index, item in enumerate(self._release_items(), 1):
            card = self._rounded_rectangle(48, y, 438, y + 52, 10,
                                           fill="#101214", outline=LINE)
            icon_plate = self._rounded_rectangle(60, y + 9, 94, y + 43, 8,
                                                 fill=PANEL, outline="#45494e")
            try:
                icon = tk.PhotoImage(file=str(self.content_root / item["icon"]))
                self._images.append(icon)
                icon_item = self.canvas.create_image(self._p(77), self._p(y + 26),
                                         image=icon, anchor="center")
            except (KeyError, OSError, tk.TclError):
                icon_item = self._text(77, y + 26, "◇", size=13,
                                       anchor="center")
            title = self._text(108, y + 26, item["title"], size=10,
                               weight="bold", width=275, anchor="w")
            number = self._text(420, y + 26, f"0{index}", size=7,
                                color=MUTED, weight="bold", anchor="e")
            for element in (card, icon_plate, icon_item, title, number):
                self.canvas.tag_bind(
                    element, "<Enter>",
                    lambda _event, target=card: self.canvas.itemconfigure(
                        target, fill="#171a1d"))
                self.canvas.tag_bind(
                    element, "<Leave>",
                    lambda _event, target=card: self.canvas.itemconfigure(
                        target, fill="#101214"))
            y += 62
        self._button(48, 374, 158, 34, "Continue", self.close, primary=True)
        self._button(218, 374, 190, 34, "View Full Changelog",
                     lambda: webbrowser.open(CHANGELOG_URL, new=2))
        self._build_preference_toggle(48, 454)

    def _build_preference_toggle(self, x, y):
        self._preference_track = self._rounded_rectangle(
            x, y, x + 36, y + 20, 10, fill=PANEL, outline=LINE)
        self._preference_knob = self.canvas.create_oval(
            self._p(x + 3), self._p(y + 3), self._p(x + 17), self._p(y + 17),
            fill=MUTED, outline="")
        self._preference_label = self._text(
            x + 48, y + 10, "Show this after updates", size=8,
            color=MUTED, anchor="w")
        for item in (self._preference_track, self._preference_knob,
                     self._preference_label):
            self.canvas.tag_bind(item, "<Button-1>",
                                 lambda _event: self._toggle_preference())
        self._refresh_preference_toggle(x, y)

    def _toggle_preference(self):
        self._show_after_updates.set(not self._show_after_updates.get())
        self._refresh_preference_toggle(48, 454)
        self._write_preference()

    def _refresh_preference_toggle(self, x, y):
        enabled = self._show_after_updates.get()
        self.canvas.itemconfigure(self._preference_track,
                                  fill=WHITE if enabled else PANEL)
        knob_x = x + 19 if enabled else x + 3
        self.canvas.coords(self._preference_knob,
                           self._p(knob_x), self._p(y + 3),
                           self._p(knob_x + 14), self._p(y + 17))
        self.canvas.itemconfigure(self._preference_knob,
                                  fill=BG if enabled else MUTED)

    def _rounded_rectangle(self, x1, y1, x2, y2, radius, **options):
        points = (
            self._p(x1 + radius), self._p(y1),
            self._p(x2 - radius), self._p(y1),
            self._p(x2), self._p(y1), self._p(x2), self._p(y1 + radius),
            self._p(x2), self._p(y2 - radius),
            self._p(x2), self._p(y2), self._p(x2 - radius), self._p(y2),
            self._p(x1 + radius), self._p(y2),
            self._p(x1), self._p(y2), self._p(x1), self._p(y2 - radius),
            self._p(x1), self._p(y1 + radius),
            self._p(x1), self._p(y1), self._p(x1 + radius), self._p(y1),
        )
        return self.canvas.create_polygon(
            points, smooth=True, splinesteps=24, **options)

    def _button(self, x, y, width, height, label, command, *, primary=False):
        fill = WHITE if primary else PANEL
        color = BG if primary else WHITE
        rectangle = self._rounded_rectangle(
            x, y, x + width, y + height, min(10, height / 2), fill=fill,
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
