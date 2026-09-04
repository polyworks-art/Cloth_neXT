# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Product-grade, non-blocking Welcome and What's-New Companion windows."""
from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
import webbrowser

from cloth_next.onboarding import (default_resource_root, load_welcome,
                                   load_whats_new)

PAGE = "#f7f9fc"
CARD = "#ffffff"
INK = "#101a35"
MUTED = "#5d6c8b"
ACCENT = "#2f6df6"
ACCENT_HOVER = "#2058d8"
ICON_BLUE = "#2f6df6"
BORDER = "#d8dfec"
SOFT = "#eef3fb"


def load_content(mode: str, version: str | None = None,
                 content_root: Path | None = None) -> dict:
    root = content_root or default_resource_root()
    if mode == "welcome":
        return load_welcome(root)
    if mode == "whats-new" and version:
        return load_whats_new(version, root)
    raise ValueError("What's New requires --version MAJOR.MINOR.PATCH")


class InfoWindow:
    WIDTH = 580
    HEIGHT = 390
    HERO_WIDTH = 175

    def __init__(self, mode: str, content: dict, *, root=None,
                 content_root: Path | None = None):
        self.mode = mode
        self.content = content
        self.content_root = content_root or default_resource_root()
        self.root = root or tk.Tk()
        self.root.withdraw()
        self.root.title("Welcome to Cloth NeXt" if mode == "welcome"
                        else content["title"])
        self.root.configure(bg=PAGE)
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.root.minsize(self.WIDTH, self.HEIGHT)
        self.root.resizable(False, False)
        self._logo = None
        self._brand_logo = None
        self._hero_image = None
        self._highlight_icons = []
        try:
            from companion.app import _asset, _match_windows_title_bar, _windows_identity
            _windows_identity()
            self._logo = tk.PhotoImage(file=str(_asset("cloth_next.png")))
            self._brand_logo = self._logo.subsample(11, 11)
            self.root.iconphoto(True, self._logo)
            _match_windows_title_bar(self.root, light=True)
        except (ImportError, tk.TclError):
            pass
        self._build()
        self.root.update_idletasks()
        self._center()
        self.root.deiconify()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        auto_close = os.environ.get("CLOTH_NEXT_COMPANION_AUTO_CLOSE_MS", "")
        if auto_close.isdigit() and int(auto_close) > 0:
            self.root.after(int(auto_close), self.close)

    def _center(self):
        width = max(self.WIDTH, self.root.winfo_width())
        height = max(self.HEIGHT, self.root.winfo_height())
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _label(self, parent, text, *, size=10, color=INK, weight="normal",
               wrap=650, **kwargs):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                        font=("Segoe UI", size, weight), justify="left",
                        anchor="w", wraplength=wrap, **kwargs)

    def _build(self):
        shell = tk.Frame(self.root, bg=PAGE)
        shell.pack(fill="both", expand=True)
        self._build_hero(shell)
        content_shell = tk.Frame(shell, bg=PAGE)
        content_shell.pack(side="left", fill="both", expand=True)
        page = tk.Frame(content_shell, bg=PAGE, padx=16, pady=9)
        page.pack(fill="both", expand=True)

        eyebrow = "CLOTH NEXT  •  GETTING STARTED" if self.mode == "welcome" else (
            f"CLOTH NEXT  •  VERSION {self.content['version']}")
        self._label(page, eyebrow, size=7, color=ACCENT,
                    weight="bold").pack(fill="x")
        self._label(page, self.content["title"], size=15,
                    weight="bold", wrap=360).pack(fill="x", pady=(2, 2))
        self._label(page, self.content["subtitle"], size=8, color=MUTED,
                    wrap=360).pack(fill="x", pady=(0, 4))
        tk.Frame(page, height=2, bg=ACCENT).pack(fill="x", pady=(0, 4))

        if self.mode == "welcome":
            self._welcome_body(page)
        else:
            self._whats_new_body(page)
        self._actions(page)
        footer = tk.Frame(page, bg=PAGE)
        footer.pack(fill="x", pady=(3, 0))
        self._label(footer, "Cloth NeXt for Blender", size=9,
                    color=MUTED).pack(side="left")
        if self.mode == "whats-new":
            self._label(footer, self.content["version"], size=9,
                        color=MUTED).pack(side="right")

    def _build_hero(self, shell):
        width = self.HERO_WIDTH
        hero = tk.Canvas(shell, width=width, bg="#252729", highlightthickness=0)
        hero.pack(side="left", fill="y")
        hero.create_rectangle(0, 0, width, self.HEIGHT, fill="#252729", outline="")
        asset = self.content.get("hero_asset")
        if asset:
            try:
                self._hero_image = tk.PhotoImage(
                    file=str(self.content_root / asset))
                hero.create_image(0, 0, image=self._hero_image, anchor="nw")
            except (OSError, ValueError, tk.TclError):
                asset = None
        if not asset:
            hero.create_polygon(-20, 280, 45, 160, 115, 210, 220, 120, 220, 480,
                                -20, 480, fill="#303235", outline="", smooth=True)
            hero.create_polygon(-20, 360, 60, 250, 120, 290, 220, 210, 220, 480,
                                -20, 480, fill="#3a3937", outline="", smooth=True)
            hero.create_line(-20, 360, 58, 250, 120, 290, 220, 210,
                             fill="#d88724", width=3, smooth=True)
        if self._brand_logo is not None:
            hero.create_image(14, 17, image=self._brand_logo, anchor="nw")
        else:
            hero.create_text(14, 19, text="◆", fill="#2a91e8",
                             font=("Segoe UI Symbol", 14), anchor="nw")
        hero.create_text(43, 21, text="Cloth NeXt", fill="#ffffff",
                         font=("Segoe UI", 11, "bold"), anchor="nw")
        hero.create_text(14, 51, text="SIMULATION FOR ARTISTS", fill="#b9b9b9",
                         font=("Segoe UI", 6, "bold"), anchor="nw")
        hero.create_text(14, self.HEIGHT - 30,
                         text="Create freely.\nBake with confidence.",
                         fill="#e8e5df", font=("Segoe UI", 7),
                         anchor="sw", justify="left")
        hero.create_text(width - 12, self.HEIGHT - 10, text="© Tim Christmann",
                         fill="#c7c4bf", font=("Segoe UI", 7), anchor="se")

    def _section_title(self, parent, text):
        self._label(parent, text, size=9, weight="bold").pack(fill="x", pady=(0, 3))

    def _welcome_body(self, page):
        self._section_title(page, "Getting Started")
        step_icons = ("icons/link.png", "icons/cloth.png", "icons/play.png")
        for index, step in enumerate(self.content["steps"], 1):
            card = tk.Frame(page, bg=CARD, padx=8, pady=5,
                            highlightbackground=BORDER, highlightthickness=1)
            card.pack(fill="x", pady=(0, 5))
            icon = None
            try:
                icon = tk.PhotoImage(file=str(self.content_root / step_icons[index - 1]))
                self._highlight_icons.append(icon)
            except (AttributeError, OSError, tk.TclError):
                pass
            badge = tk.Label(card, image=icon, text=str(index) if icon is None else "",
                             width=24, height=24, bg=CARD, fg=ACCENT,
                             font=("Segoe UI", 8, "bold"))
            badge.pack(side="left", anchor="n", padx=(0, 7))
            copy = tk.Frame(card, bg=CARD)
            copy.pack(side="left", fill="x", expand=True)
            self._label(copy, step["title"], size=8,
                        weight="bold", wrap=310).pack(fill="x")
            self._label(copy, step["description"], size=7, color=MUTED,
                        wrap=310).pack(fill="x", pady=(1, 0))

    def _whats_new_body(self, page):
        self._section_title(page, "Major Highlights")
        grid = tk.Frame(page, bg=PAGE)
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1, uniform="highlights")
        for index, item in enumerate(self.content["highlights"]):
            card = tk.Frame(grid, bg=CARD, padx=7, pady=5,
                            highlightbackground=BORDER, highlightthickness=1)
            card.grid(row=index // 2, column=index % 2, sticky="nsew",
                      padx=(0, 3) if index % 2 == 0 else (3, 0), pady=(0, 3))
            icon = None
            try:
                icon = tk.PhotoImage(file=str(self.content_root / item["icon"]))
                self._highlight_icons.append(icon)
            except (AttributeError, OSError, tk.TclError):
                pass
            tk.Label(card, image=icon, text="•" if icon is None else "",
                     bg=CARD, fg=ICON_BLUE, font=("Segoe UI", 10, "bold")).pack(anchor="w")
            self._label(card, item["title"], size=8, weight="bold",
                        wrap=155).pack(fill="x", pady=(0, 1))
            if item["description"]:
                self._label(card, item["description"], size=7, color=MUTED,
                            wrap=155).pack(fill="x")
        for title, key in (("Improvements", "improvements"),
                           ("Stability & Fixes", "fixes")):
            values = self.content[key]
            if not values:
                continue
            self._section_title(page, title)
            box = tk.Frame(page, bg=SOFT, padx=7, pady=2)
            box.pack(fill="x", pady=(0, 3))
            for value in values:
                line = tk.Frame(box, bg=SOFT)
                line.pack(fill="x")
                icon = None
                try:
                    icon = tk.PhotoImage(file=str(
                        self.content_root / value["icon"]))
                    self._highlight_icons.append(icon)
                except (OSError, tk.TclError):
                    pass
                tk.Label(line, image=icon, text="•" if icon is None else "",
                         bg=SOFT, fg=ICON_BLUE).pack(side="left", padx=(0, 4))
                self._label(line, value["text"], size=7, color=MUTED,
                            wrap=325).pack(side="left", fill="x", expand=True)

    def _actions(self, page):
        row = tk.Frame(page, bg=PAGE)
        row.pack(fill="x", pady=(3, 0))
        for index, action in enumerate(self.content["actions"]):
            primary = index == 0
            button = tk.Button(
                row, text=action["label"], command=lambda value=action: self._activate(value),
                bg=ACCENT if primary else CARD, fg="white" if primary else INK,
                activebackground=ACCENT_HOVER if primary else SOFT,
                activeforeground="white" if primary else INK,
                relief="flat" if primary else "solid", borderwidth=1,
                highlightbackground=BORDER, padx=6, pady=4,
                font=("Segoe UI", 7, "bold" if primary else "normal"),
                cursor="hand2", takefocus=True)
            button.pack(side="left", padx=(0, 9))

    def _activate(self, action):
        if action["kind"] == "url":
            webbrowser.open(action["url"], new=2)
        else:
            self.close()

    def close(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def run_info_window(mode: str, version: str | None = None,
                    content_root: Path | None = None) -> None:
    root = content_root or default_resource_root()
    InfoWindow(mode, load_content(mode, version, root),
               content_root=root).run()
