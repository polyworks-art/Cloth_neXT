# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""PolyWorks account and services window of the Cloth NeXt Companion.

PolyWorks covers optional services (GAIA, cloud bakes). Cloth NeXt Classic is
purchased, licensed and updated through Superhive and needs no account here.

This is the only interactive account UI of Cloth NeXt: sign-in, sign-out,
activation, devices, activation codes and portal links. Blender shows a
read-only summary of what this window publishes. Network work runs on one
worker thread; Tk is only touched on the Tk thread.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
import queue
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
import webbrowser

from cloth_next.onboarding import default_resource_root
from cloth_next.polyworks import messages
from cloth_next.polyworks.service import AccountService, DeviceLink, MfaChallenge, ServiceError, default_label
from cloth_next.polyworks.status import LicenseStatus, format_time
from cloth_next.polyworks.storage import PRESENCE_FILE, StorageError
from companion.onboarding_window import (BG, LINE, MUTED, ONBOARDING_FONT as FONT, PANEL as CARD,
                                         WHITE as FG, InfoWindow, _register_onboarding_fonts)

LOG = logging.getLogger(__name__)
# Results that leave everything working: shown as a notice, not as an error.
SOFT_ERRORS = frozenset({"authorization_pending", "network_unavailable", "service_unavailable",
                         "server_error", "rate_limited"})
SIGNED_IN = "Signed in. Your PolyWorks service licenses are shown below."
SIGNED_OUT_DEVICES = "Sign in to see the devices activated with your service license."
HEARTBEAT_MS = 5000
RENDER_MS = 30000
SYNC_CHECK_MS = 5 * 60 * 1000
OK = "#b9c9c5"
WARN = "#d8c38f"
BAD = "#e39a8f"
STATUS_COLORS = {LicenseStatus.ACTIVE: OK, LicenseStatus.OFFLINE_VERIFIED: OK,
                 LicenseStatus.EXPIRED: BAD, LicenseStatus.REVOKED: BAD,
                 LicenseStatus.SUSPENDED: BAD, LicenseStatus.INVALID: BAD}


def _signed_in(view) -> bool:
    return view.account_label == "Signed in"


def _can_refresh(view) -> bool:
    return view.account_label != "Not signed in" or view.expires_at is not None


# Status texts are shared with Blender, where they point to the Companion. In the
# Companion itself they point to the matching part of this window instead.
IN_WINDOW = (
    ("sign in through Cloth NeXt Companion to use GAIA and online services.",
     "sign in above to use GAIA and online services."),
    ("Activate this computer in Cloth NeXt Companion to use PolyWorks services.",
     "Select a service license and activate this computer under Devices."),
    ("Manage your devices in Cloth NeXt Companion.", "Free a device slot under Devices."),
    ("Open Cloth NeXt Companion to activate again.", "Activate this computer again under Devices."),
    ("Open Cloth NeXt Companion for details.", "Check your PolyWorks account for details."),
    ("and open Cloth NeXt Companion to renew it.", "and press Refresh to renew it."),
    ("connect to the internet and open Cloth NeXt Companion before",
     "connect to the internet and press Refresh before"),
    ("Sign in again through Cloth NeXt Companion.", "Sign in again above."),
    ("Cloth NeXt Companion has not loaded your service licenses yet.",
     "Your service licenses are not loaded yet."),
    ("Sign in again in Cloth NeXt Companion", "Sign in again above"),
    ("sign in again in Cloth NeXt Companion", "sign in again above"),
    (" in Cloth NeXt Companion", " here"),
)


def in_window(text: str) -> str:
    for old, new in IN_WINDOW:
        text = text.replace(old, new)
    return text


def _plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


class WelcomeButton(tk.Canvas):
    """The Welcome screen's rounded monochrome button."""

    def __init__(self, parent, label, command, *, primary=False):
        self.label, self.command, self.primary = label, command, primary
        self.enabled = True
        self.font = tkfont.Font(family=FONT, size=8, weight="bold")
        super().__init__(parent, width=self.font.measure(label) + 30, height=29,
                         bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2")
        self.canvas = self
        self.bind("<Configure>", lambda _: self._paint())
        self.bind("<Enter>", lambda _: self._paint(hover=True))
        self.bind("<Leave>", lambda _: self._paint())
        self.bind("<Button-1>", lambda _: self.invoke())
        self.bind("<Return>", lambda _: self.invoke())
        self.configure(takefocus=True)

    def invoke(self):
        if self.enabled:
            self.command()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.configure(cursor="hand2" if self.enabled else "arrow", takefocus=self.enabled)
        self._paint()

    def _paint(self, hover=False):
        self.delete("all")
        width, height = max(self.winfo_width(), self.winfo_reqwidth()), 29
        if self.primary and self.enabled:
            fill = "#dededb" if hover else FG
        else:
            fill = "#202326" if hover and self.enabled else CARD
        InfoWindow._rounded_rectangle(self, 1, 1, width - 1, height - 1, 8, fill=fill,
                                      outline=FG if self.primary and self.enabled else LINE, width=1)
        color = (BG if self.primary else FG) if self.enabled else "#666b72"
        self.create_text(width / 2, height / 2, text=self.label, font=self.font, fill=color)

    def _p(self, value):
        return value


class WelcomeEntry(tk.Canvas):
    """Text field drawn like WelcomeButton: same height, radius, fill and outline."""
    HEIGHT = 29

    def __init__(self, parent, variable, *, secret=False, width=20):
        self.font = tkfont.Font(family=FONT, size=9)
        super().__init__(parent, width=self.font.measure("0") * width + 24, height=self.HEIGHT,
                         bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="xterm")
        self.canvas = self
        self.focused = False
        self.entry = tk.Entry(self, textvariable=variable, show="•" if secret else "",
                              relief="flat", bd=0, highlightthickness=0, bg=CARD, fg=FG,
                              disabledbackground=CARD, disabledforeground="#666b72",
                              insertbackground=FG, insertwidth=1, font=self.font)
        self.entry_id = self.create_window(12, self.HEIGHT / 2, window=self.entry, anchor="w")
        self.bind("<Configure>", lambda _: self._paint())
        self.bind("<Button-1>", lambda _: self.focus_set())
        self.entry.bind("<FocusIn>", lambda _: self._focus(True), add="+")
        self.entry.bind("<FocusOut>", lambda _: self._focus(False), add="+")

    # Entry-like interface used by the window and tests.
    def focus_set(self):
        self.entry.focus_set()

    def bind_key(self, sequence, callback):
        self.entry.bind(sequence, callback)

    def set_enabled(self, enabled):
        self.entry.configure(state="normal" if enabled else "disabled")
        self.configure(cursor="xterm" if enabled else "arrow")
        self._paint()

    def cget(self, key):
        return self.entry.cget(key) if key == "state" else super().cget(key)

    def _p(self, value):
        return value

    def _focus(self, focused):
        self.focused = focused
        self._paint()

    def _paint(self):
        self.delete("frame")
        width = max(self.winfo_width(), self.winfo_reqwidth())
        enabled = str(self.entry.cget("state")) == "normal"
        outline = "#738492" if self.focused and enabled else LINE
        InfoWindow._rounded_rectangle(self, 1, 1, width - 1, self.HEIGHT - 1, 8, fill=CARD,
                                      outline=outline, width=1, tags="frame")
        self.tag_lower("frame")
        self.itemconfigure(self.entry_id, width=max(1, width - 24))


class WelcomeCard(tk.Canvas):
    """Rounded card using the Welcome/What's New geometry."""
    FILL = "#101214"

    def __init__(self, parent):
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0, height=100)
        self.canvas = self
        self.body = tk.Frame(self, bg=self.FILL, padx=8, pady=3)
        self.body_id = self.create_window(12, 12, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._measure)
        self.bind("<Configure>", self._resize)

    def _p(self, value):
        return value

    def _measure(self, _event):
        height = self.body.winfo_reqheight() + 24
        if self.winfo_reqheight() != height:
            self.configure(height=height)
        self._paint()

    def _resize(self, event):
        self.itemconfigure(self.body_id, width=max(1, event.width - 24))
        self._paint()

    def _paint(self):
        self.delete("card-background")
        width, height = self.winfo_width(), self.body.winfo_reqheight() + 24
        shape = InfoWindow._rounded_rectangle(self, 1, 1, width - 1, height - 1, 8,
                                              fill=self.FILL, outline=LINE, width=1,
                                              tags="card-background")
        self.tag_lower(shape)


class AccountWindow:
    def __init__(self, parent=None, *, service: AccountService | None = None,
                 content_root: Path | None = None, owns_presence: bool = True):
        self.owns_root = parent is None
        # The tray publishes presence itself while it lives; its window must not.
        self.owns_presence = owns_presence
        if self.owns_root:
            try:  # before the first window, or the taskbar keeps the default icon
                from companion.app import _windows_identity
                _windows_identity()
            except ImportError:
                pass
        self.root = parent or tk.Tk()
        if self.owns_root:
            self.root.withdraw()
        self.window = tk.Toplevel(self.root)
        self.content_root = content_root or default_resource_root()
        _register_onboarding_fonts(self.content_root)
        self.service = service or AccountService()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="polyworks-account")
        self.events: queue.Queue = queue.Queue()
        self.closed = False
        self.busy = False
        self.link: DeviceLink | None = None
        self.challenge: MfaChallenge | None = None
        self.view = None
        self.licenses: list = []
        self.devices: list = []
        self.devices_for = None
        self.devices_failed = False
        self.devices_shown_for = None
        self.activated_license = None
        self._after: list = []
        self.controls: list = []

        self._build_window()
        self._build_account()
        self._build_license()
        self._build_devices()
        self._build_code()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._heartbeat()
        self._schedule(80, self._drain)
        self._schedule(RENDER_MS, self._periodic_render)
        self._schedule(SYNC_CHECK_MS, self._periodic_sync)
        self._render(self.service.view())
        self.submit(self._initial_refresh, quiet=True)
        auto_close = os.environ.get("CLOTH_NEXT_COMPANION_AUTO_CLOSE_MS", "")
        if auto_close.isdigit():  # packaged-EXE smoke tests only
            self._schedule(int(auto_close), self.close)

    # Window scaffolding -----------------------------------------------------------
    def _build_window(self):
        window = self.window
        window.title("PolyWorks Account — Cloth NeXt")
        window.configure(bg=BG)
        try:
            from companion.app import _asset, _match_windows_title_bar
            # Square app icon as in the Bake window (the wide logo shows as default).
            icon = tk.PhotoImage(file=str(_asset("cloth_next.png")))
            self._app_icon = icon
            window.iconphoto(True, icon)
            _match_windows_title_bar(window, light=False, background_color=0x00000000)
        except (ImportError, tk.TclError, AttributeError):
            pass
        width, height = 620, min(860, window.winfo_screenheight() - 100)
        x = max(0, (window.winfo_screenwidth() - width) // 2)
        y = max(0, (window.winfo_screenheight() - height) // 2 - 25)
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.minsize(500, 420)
        footer = tk.Frame(window, bg=BG, padx=28, pady=14)
        footer.pack(side="bottom", fill="x")
        self.connection = tk.StringVar(value="")
        tk.Label(footer, textvariable=self.connection, bg=BG, fg=MUTED, font=(FONT, 9),
                 justify="left", anchor="w", wraplength=420).pack(side="left", fill="x", expand=True)
        WelcomeButton(footer, "Close", self.close, primary=True).pack(side="right")
        canvas = tk.Canvas(window, bg=BG, highlightthickness=0)
        style = ttk.Style(window)
        for part in ("trough", "thumb"):
            element = f"PolyWorks.Vertical.Scrollbar.{part}"
            if element not in style.element_names():
                style.element_create(element, "from", "clam", f"Vertical.Scrollbar.{part}")
        style.layout("PolyWorks.Vertical.TScrollbar", [
            ("PolyWorks.Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("PolyWorks.Vertical.Scrollbar.thumb", {"sticky": "nswe", "expand": True})]})])
        style.configure("PolyWorks.Vertical.TScrollbar", background=LINE, troughcolor=BG,
                        bordercolor=BG, lightcolor=LINE, darkcolor=LINE, borderwidth=0,
                        relief="flat", width=8, gripcount=0)
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=canvas.yview,
                                  style="PolyWorks.Vertical.TScrollbar", takefocus=False)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        self.body = tk.Frame(canvas, bg=BG, padx=28, pady=22)
        body_id = canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(body_id, width=event.width))
        window.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"))
        header = tk.Canvas(self.body, height=120, bg=BG, highlightthickness=0)
        header.pack(fill="x", pady=(0, 10))
        self._images = []
        try:
            logo = tk.PhotoImage(file=str(self.content_root / "assets/cloth-next-logo-ui-crisp.png"))
            self._images.append(logo)
            header.create_image(4, 5, image=logo, anchor="nw")
        except (OSError, tk.TclError):
            pass
        header.create_text(66, 12, text="PolyWorks Account", anchor="nw", fill=FG,
                           font=(FONT, 22, "bold"))
        header.create_text(4, 66, text="Manage your PolyWorks account for GAIA and online services.",
                           anchor="nw", fill=MUTED, font=(FONT, 10))
        header.create_text(4, 90, text="Optional: Cloth NeXt Classic is licensed through Superhive "
                           "and needs no PolyWorks account.", anchor="nw",
                           fill=MUTED, font=(FONT, 8))
        self.message = tk.StringVar(value="")
        self.message_label = tk.Label(self.body, textvariable=self.message, justify="left",
                                      wraplength=520, bg=BG, fg=WARN, font=(FONT, 9), anchor="w")
        self.message_label.pack(fill="x", pady=(0, 8))

    def _card(self, number, title):
        surface = WelcomeCard(self.body)
        surface.pack(fill="x", pady=(0, 12))
        card = surface.body
        tk.Label(card, text=f"{number}  /  {title}", bg=card.cget("bg"), fg=FG, anchor="w",
                 font=(FONT, 11, "bold")).pack(fill="x", pady=(0, 8))
        return card

    def _text(self, parent, variable=None, text="", *, color=MUTED, size=10, bold=False):
        label = tk.Label(parent, text=text, textvariable=variable, bg=parent.cget("bg"), fg=color,
                         anchor="w", justify="left", wraplength=500,
                         font=(FONT, size, "bold" if bold else "normal"))
        label.pack(fill="x", pady=2)
        return label

    def _row(self, parent):
        row = tk.Frame(parent, bg=parent.cget("bg"))
        row.pack(fill="x", pady=(8, 2))
        return row

    def _button(self, parent, label, command, *, primary=False, when=None, side="left"):
        button = WelcomeButton(parent, label, command, primary=primary)
        button.pack(side=side, padx=(0, 8))
        self.controls.append((button, when or (lambda view: True)))
        return button

    def _entry(self, parent, variable, *, secret=False, width=None):
        return WelcomeEntry(parent, variable, secret=secret, width=width or 20)

    # Sections -------------------------------------------------------------------------
    def _build_account(self):
        card = self._card("01", "Account")
        self.account_status = tk.StringVar()
        self.account_detail = tk.StringVar()
        self.account_status_label = self._text(card, self.account_status, color=FG, size=11, bold=True)
        self._text(card, self.account_detail, size=9)
        signed_out = lambda view: not _signed_in(view)  # noqa: E731
        row = self._row(card)
        self._button(row, "Sign in with browser", self.sign_in_browser, primary=True, when=signed_out)
        self._button(row, "Sign in with email", self.toggle_password, when=signed_out)
        self._button(row, "Sign out", self.sign_out, when=_signed_in)
        row = self._row(card)
        self._button(row, "Refresh", self.refresh, when=_can_refresh)
        self._button(row, "Open PolyWorks account", self.open_portal)

        self.link_frame = tk.Frame(card, bg=card.cget("bg"))
        self.link_code = tk.StringVar()
        self._text(self.link_frame, text="Approve this sign-in in your browser. Your code:")
        tk.Label(self.link_frame, textvariable=self.link_code, bg=card.cget("bg"), fg=FG,
                 font=(FONT, 20, "bold"), anchor="w").pack(fill="x", pady=(2, 4))
        link_row = self._row(self.link_frame)
        WelcomeButton(link_row, "Open browser again", self._open_link).pack(side="left", padx=(0, 8))
        WelcomeButton(link_row, "Cancel sign-in", self.cancel_sign_in).pack(side="left")

        self.password_frame = tk.Frame(card, bg=card.cget("bg"))
        self.email = tk.StringVar()
        self.password = tk.StringVar()
        self.mfa_code = tk.StringVar()
        self._text(self.password_frame, text="Email address", size=9)
        self.email_entry = self._entry(self.password_frame, self.email, width=40)
        self.email_entry.pack(fill="x")
        self._text(self.password_frame, text="Password", size=9)
        self.password_entry = self._entry(self.password_frame, self.password, secret=True, width=40)
        self.password_entry.pack(fill="x")
        self.password_entry.bind_key("<Return>", lambda _: self.sign_in_password())
        password_row = self._row(self.password_frame)
        WelcomeButton(password_row, "Sign in", self.sign_in_password, primary=True).pack(side="left")
        self._text(self.password_frame, text="Accounts with passkeys only: use “Sign in with "
                   "browser”. Your password is sent to PolyWorks and never stored.", size=8)

        self.mfa_frame = tk.Frame(card, bg=card.cget("bg"))
        self._text(self.mfa_frame, text="Enter the code from your authenticator app or a recovery code.")
        mfa_row = self._row(self.mfa_frame)
        self.mfa_entry = self._entry(mfa_row, self.mfa_code, width=16)
        self.mfa_entry.pack(side="left", padx=(0, 8))
        self.mfa_entry.bind_key("<Return>", lambda _: self.submit_mfa())
        WelcomeButton(mfa_row, "Verify", self.submit_mfa, primary=True).pack(side="left")

    def _build_license(self):
        card = self._card("02", "Service license")
        self.license_status = tk.StringVar()
        self.license_detail = tk.StringVar()
        self.license_status_label = self._text(card, self.license_status, color=FG, size=11, bold=True)
        self._text(card, self.license_detail, size=9)
        self.license_list = tk.Frame(card, bg=card.cget("bg"))
        self.license_list.pack(fill="x", pady=(4, 0))
        self.selected_license = tk.StringVar()

    def _build_devices(self):
        card = self._card("03", "Devices")
        self._text(card, text="This computer", color=FG, bold=True)
        self._text(card, text="Name shown in your device list", size=9)
        self.device_label = tk.StringVar(value=default_label())
        self.label_entry = self._entry(card, self.device_label, width=40)
        self.label_entry.pack(fill="x")
        row = self._row(card)
        self._button(row, "Activate this computer", self.activate, primary=True,
                     when=self._can_activate)
        self.activation_hint = tk.StringVar()
        self._text(card, self.activation_hint, size=8)
        tk.Frame(card, bg=LINE, height=1).pack(fill="x", pady=(10, 8))
        self.devices_hint = tk.StringVar(value=SIGNED_OUT_DEVICES)
        self._text(card, self.devices_hint, size=9)
        self.device_list = tk.Frame(card, bg=card.cget("bg"))
        self.device_list.pack(fill="x")
        row = self._row(card)
        self._button(row, "Reload devices", self.load_devices, when=self._has_usable_selection)

    def _selected(self):
        return next((item for item in self.licenses
                     if item["id"] == self.selected_license.get()), None)

    def _has_usable_selection(self, view) -> bool:
        selected = self._selected()
        return _signed_in(view) and selected is not None and bool(selected["usable"])

    def _activated_here(self, view) -> bool:
        """This computer holds a verified licence for the selected licence."""
        return view.authorized and self.activated_license == self.selected_license.get()

    def _can_activate(self, view) -> bool:
        return self._has_usable_selection(view) and not self._activated_here(view)

    def _build_code(self):
        card = self._card("04", "Activation code")
        self._text(card, text="Received an activation code from PolyWorks? Redeem it for your account.",
                   size=9)
        self.code = tk.StringVar()
        row = self._row(card)
        self.code_entry = self._entry(row, self.code, width=28)
        self.code_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._button(row, "Redeem code", self.redeem, when=_signed_in)

    # Rendering ----------------------------------------------------------------------
    def _render(self, view):
        self.view = view
        state = self.service.state()
        self.licenses = state["licenses"] or []
        self.activated_license = (self.service._current_activation(state)[0]
                                  if view.authorized else None)
        self.account_status.set(view.account_label + (f"  ·  {view.account_identity}"
                                                      if view.account_identity else ""))
        storage = state["account"]["storage"]
        self.account_detail.set(f"Session: {in_window(view.session_label)}" +
                                (f"\nSession stored in: {storage}" if storage and
                                 view.account_label.startswith("Signed in") else ""))
        self.license_status.set(view.label + (f"  ·  {view.tier}" if view.tier else ""))
        self.license_status_label.configure(fg=STATUS_COLORS.get(view.status, FG))
        lines = [in_window(line) for line in (view.message, *view.warnings) if line]
        if view.expires_at:
            lines.append(f"Offline license: {view.offline_authorization}")
        self.license_detail.set("\n".join(lines))
        self._render_licenses()
        self._render_activation(view)
        if not _signed_in(view):
            self.devices, self.devices_for, self.devices_shown_for = [], None, None
        if self.devices_for is None:
            self._render_devices()
        online = {True: "Online", False: "Offline", None: "Not checked yet"}[view.online]
        self.connection.set(f"PolyWorks service: {online}  ·  Last license sync: "
                            f"{format_time(view.last_sync_at)}")
        self._update_controls()

    def _render_licenses(self):
        for child in self.license_list.winfo_children():
            child.destroy()
        if not self.licenses:
            return
        usable = [item["id"] for item in self.licenses if item["usable"]]
        if self.selected_license.get() not in usable:
            preferred = self.activated_license if self.activated_license in usable else None
            self.selected_license.set(preferred or (usable[0] if usable else ""))
        for item in self.licenses:
            seats = f" · {_plural(item['seats'], 'seat')}" if item.get("seats") else ""
            devices = (f" · {item['devices_active']}/{item['device_limit']} devices"
                       if item.get("device_limit") is not None else "")
            state = "" if item["usable"] else f" · {item.get('state') or 'unavailable'}"
            if item.get("purchase_status") and item["purchase_status"] != "valid":
                state += f" ({item['purchase_status']})"
            text = f"{(item.get('tier') or 'License').title()}{seats}{devices}{state}"
            tk.Radiobutton(self.license_list, text=text, value=item["id"],
                           variable=self.selected_license, command=self._license_selected,
                           state="normal" if item["usable"] else "disabled",
                           bg=self.license_list.cget("bg"), disabledforeground=MUTED,
                           fg=FG, selectcolor="#0d1013",
                           activebackground=self.license_list.cget("bg"), activeforeground=FG,
                           font=(FONT, 10), anchor="w", highlightthickness=0).pack(fill="x")

    def _render_activation(self, view):
        if not _signed_in(view):
            text = "Sign in to activate this computer."
        elif not self.licenses:
            text = "Your account has no license to activate yet."
        elif self._selected() is None:
            text = "None of your licenses can be activated right now."
        elif self._activated_here(view):
            text = "This computer is activated with the selected license."
        elif view.authorized:
            text = "This computer uses another license. Activating switches it to the selected one."
        else:
            text = "Activates the license selected above on this computer."
        self.activation_hint.set(text)

    def _license_selected(self):
        if self.devices_for != self.selected_license.get():
            self.devices, self.devices_for, self.devices_shown_for = [], None, None
            self._render_devices()
        if self.view is not None:
            self._render_activation(self.view)
        self._update_controls()
        self._maybe_load_devices()

    def _maybe_load_devices(self):
        """Keep the device list of the selected licence loaded while signed in."""
        if (self.busy or self.link is not None or self.view is None
                or not self._has_usable_selection(self.view)
                or self.devices_for == self.selected_license.get()):
            return
        self.load_devices(quiet=True)

    def _render_devices(self):
        for child in self.device_list.winfo_children():
            child.destroy()
        self.device_list.configure(height=1)  # an emptied Tk frame keeps its old height
        installation = self.service.store.read_installation_id()
        if self.view is None or not _signed_in(self.view):
            self.devices_hint.set(SIGNED_OUT_DEVICES)
            return
        if self.devices_for is None:
            self.devices_hint.set("Loading devices…" if self._has_usable_selection(self.view)
                                  else "Select a usable license to see its devices.")
            return
        if self.devices_failed:
            self.devices_hint.set("The device list could not be loaded right now. "
                                  + ("Last known devices:" if self.devices else "Try Reload devices later."))
        elif not self.devices:
            self.devices_hint.set("No devices are activated with this license yet.")
            return
        else:
            self.devices_hint.set("Devices activated with the selected license:")
        for device in self.devices:
            row = tk.Frame(self.device_list, bg=self.device_list.cget("bg"))
            row.pack(fill="x", pady=2)
            mine = device["installation_id"] == installation
            seen = device.get("last_seen_at") or device.get("activated_at") or ""
            text = (device.get("label") or "Unnamed device") + ("  (this computer)" if mine else "")
            if seen:
                text += f"\nLast seen {seen[:10]}"
            tk.Label(row, text=text, bg=row.cget("bg"), fg=FG if mine else MUTED, anchor="w",
                     justify="left", font=(FONT, 9)).pack(side="left", fill="x", expand=True)
            button = WelcomeButton(row, "Deactivate",
                                   lambda d=device: self.deactivate(d))
            button.pack(side="right")
            self.controls.append((button, _signed_in))
        self._update_controls()

    def _update_controls(self):
        view = self.view
        busy = self.busy or self.link is not None
        self.controls = [(b, w) for b, w in self.controls if b.winfo_exists()]
        for button, when in self.controls:
            button.set_enabled(not busy and view is not None and when(view))
        signed_in = view is not None and _signed_in(view)
        for entry, needs_account in ((self.code_entry, True), (self.label_entry, True),
                                     (self.email_entry, False), (self.password_entry, False),
                                     (self.mfa_entry, False)):
            entry.set_enabled(not busy and (signed_in if needs_account else not signed_in))

    def show_message(self, text, *, error=False, ok=False):
        self.message.set(text)
        self.message_label.configure(fg=BAD if error else OK if ok else WARN)

    # Worker plumbing -------------------------------------------------------------------
    def submit(self, operation, *, done=None, quiet=False):
        if self.closed or self.busy:
            return False
        if not quiet:
            self.show_message("")
        self.busy = True
        self._update_controls()

        def work():
            result = error = None
            try:
                with self.service.operation():
                    result = operation()
            except ServiceError as exc:
                error = exc
            except StorageError:
                error = ServiceError("storage_unavailable")
            except Exception as exc:  # never show tracebacks; never log arguments
                LOG.error("account operation failed: %s", type(exc).__name__)
                error = ServiceError("internal_client_error")
            try:
                view = self.service.view()
            except Exception:
                view = None
            self.events.put((done, result, error, view, quiet))
        self.pool.submit(work)
        return True

    def _drain(self):
        try:
            while True:
                done, result, error, view, quiet = self.events.get_nowait()
                self.busy = False
                if view is not None:
                    self._render(view)
                if error is not None and not (quiet and error.code in ("sign_in_required",)):
                    text = (str(error) if error.code != "internal_client_error" else
                            "Something went wrong in Cloth NeXt Companion. Nothing was changed; "
                            "please try again.")
                    self.show_message(text, error=error.code not in SOFT_ERRORS)
                if done is not None:
                    done(result, error)
                self._update_controls()
        except queue.Empty:
            pass
        self._maybe_load_devices()
        self._schedule(80, self._drain)

    def _schedule(self, delay, callback):
        if not self.closed:
            self._after.append(self.window.after(delay, callback))

    # Actions ---------------------------------------------------------------------------------
    def _initial_refresh(self):
        if self.service._token() or self.service.store.read_entitlement():
            return self.service.refresh()
        return None

    def refresh(self):
        self.show_message("")
        self.devices_for = None  # reload the device list too
        self.submit(self.service.refresh, done=lambda r, e: e is None and self.show_message(
            "Account and license refreshed.", ok=True))

    def sign_in_browser(self):
        self.show_message("")
        self._hide_forms()
        self.submit(self.service.start_device_link, done=self._link_started)

    def _link_started(self, link, error):
        if error is not None or link is None:
            return
        self.link = link
        self.link_code.set(link.user_code)
        self.link_frame.pack(fill="x", pady=(10, 0))
        self._open_link()
        self.show_message(messages.COMPANION["authorization_pending"])
        self._schedule(int(link.interval * 1000), self._poll_link)
        self._update_controls()

    def _open_link(self):
        if self.link is not None:
            webbrowser.open(self.link.verification_uri_complete, new=2)

    def _poll_link(self):
        link = self.link
        if link is None or self.closed:
            return
        if self.busy:
            self._schedule(500, self._poll_link)
            return

        def finished(signed_in, error):
            if self.link is not link:
                return
            if error is not None:
                self._end_link()
                if error.code == "expired_token":
                    self.show_message(messages.COMPANION["expired_token"], error=True)
            elif signed_in:
                self._end_link()
                self.show_message(SIGNED_IN, ok=True)
            else:
                delay = max(1.0, link.next_poll - self.service.monotonic())
                self._schedule(int(delay * 1000), self._poll_link)
        self.submit(lambda: self.service.poll_device_link(link), done=finished, quiet=True)

    def _end_link(self):
        self.link = None
        self.link_frame.pack_forget()
        self._update_controls()

    def cancel_sign_in(self):
        if self.link is not None:
            self._end_link()
            self.show_message("Sign-in cancelled.")

    def toggle_password(self):
        if self.password_frame.winfo_ismapped():
            self._hide_forms()
        else:
            self._hide_forms()
            self.password_frame.pack(fill="x", pady=(10, 0))
            self.email_entry.focus_set()

    def _hide_forms(self):
        self.password_frame.pack_forget()
        self.mfa_frame.pack_forget()
        self.password.set("")
        self.mfa_code.set("")
        self.challenge = None

    def sign_in_password(self):
        email, password = self.email.get().strip(), self.password.get()
        self.password.set("")  # never keep the password in the UI
        if not email or not password:
            self.show_message("Enter your email address and password.", error=True)
            return

        def finished(challenge, error):
            if error is not None:
                return
            if challenge is not None:
                self.challenge = challenge
                self.password_frame.pack_forget()
                self.mfa_frame.pack(fill="x", pady=(10, 0))
                self.mfa_entry.focus_set()
                self.show_message("Two-factor authentication is on for this account.")
            else:
                self._hide_forms()
                self.show_message(SIGNED_IN, ok=True)
        self.submit(lambda: self.service.login_password(email, password), done=finished)

    def submit_mfa(self):
        challenge, code = self.challenge, self.mfa_code.get().strip()
        self.mfa_code.set("")
        if challenge is None or not code:
            return

        def finished(_result, error):
            if error is None:
                self._hide_forms()
                self.show_message(SIGNED_IN, ok=True)
            elif challenge.attempts_left <= 0:
                self._hide_forms()
                self.show_message("Too many incorrect codes. Sign in again.", error=True)
        self.submit(lambda: self.service.login_mfa(challenge, code), done=finished)

    def sign_out(self):
        choice = messagebox.askyesnocancel(
            "Sign out of PolyWorks",
            "Also deactivate this computer and free its device slot?\n\n"
            "Yes: sign out and deactivate this computer.\n"
            "No: sign out; this computer stays activated until its offline license needs renewal.",
            parent=self.window)
        if choice is None:
            return
        self.submit(lambda: self.service.sign_out(release_device=bool(choice)),
                    done=lambda message, error: error is None and self.show_message(message, ok=True))

    def activate(self):
        license_id, label = self.selected_license.get(), self.device_label.get()

        def finished(_view, error):
            self.devices_for = None  # the device list changed (or shows the full slots)
            if error is None:
                self.show_message("This computer is activated. Cloth NeXt in Blender shows the "
                                  "updated license automatically.", ok=True)
        self.submit(lambda: self.service.activate(license_id, label), done=finished)

    def load_devices(self, quiet=False):
        license_id = self.selected_license.get()

        def finished(devices, error):
            if license_id != self.selected_license.get():
                return  # selection changed while loading; the new one loads next
            if error is None:
                self.devices = devices
            elif self.devices_shown_for != license_id:
                self.devices = []  # never show another license's devices
            self.devices_for, self.devices_failed = license_id, error is not None
            if error is None:
                self.devices_shown_for = license_id
            self._render_devices()
        self.submit(lambda: self.service.devices(license_id), done=finished, quiet=quiet)

    def deactivate(self, device):
        license_id = self.devices_for or self.selected_license.get()
        name = device.get("label") or "Unnamed device"
        if device["installation_id"] == self.service.store.read_installation_id():
            question = (f"Deactivate this computer (“{name}”)? Its offline license is removed "
                        "now and the device slot is freed. You can activate it again later.")
        else:
            question = (f"Deactivate “{name}”? It stops receiving license renewals and frees a "
                        "device slot. Its offline license keeps working until it expires.")
        if not messagebox.askyesno("Deactivate device", question, parent=self.window):
            return

        def finished(_view, error):
            if error is None:
                self.show_message(f"“{name}” was deactivated.", ok=True)
                self.devices = [d for d in self.devices if d["id"] != device["id"]]
                self._render_devices()
        self.submit(lambda: self.service.deactivate(license_id, device["id"]), done=finished)

    def redeem(self):
        code = self.code.get().strip()
        if not code:
            return

        def finished(_view, error):
            if error is None:
                self.code.set("")
                self.show_message("Code redeemed. Activate this computer to use the new license.",
                                  ok=True)
        self.submit(lambda: self.service.redeem(code), done=finished)

    def open_portal(self):
        webbrowser.open(self.service.endpoints.portal, new=2)

    # Periodic work -------------------------------------------------------------------------
    def _heartbeat(self):
        if not self.owns_presence:
            return
        try:
            self.service.store.write_json(PRESENCE_FILE, {
                "pid": os.getpid(), "mode": "account", "heartbeat_at": time.time()})
        except OSError:
            pass
        self._schedule(HEARTBEAT_MS, self._heartbeat)

    def _periodic_render(self):
        if not self.busy:
            self._render(self.service.view())
        self._schedule(RENDER_MS, self._periodic_render)

    def _periodic_sync(self):
        if self.link is None:
            self.submit(lambda: self.service.sync_if_due(), quiet=True)
        self._schedule(SYNC_CHECK_MS, self._periodic_sync)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.link = None
        for identifier in self._after:
            try:
                self.window.after_cancel(identifier)
            except tk.TclError:
                pass
        self.pool.shutdown(wait=False, cancel_futures=True)
        if self.owns_presence:
            try:
                self.service.store.path(PRESENCE_FILE).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            self.window.destroy()
            if self.owns_root:
                self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def run_account_window(content_root: Path | None = None, *, client_name="Cloth NeXt") -> None:
    service = AccountService(client_name=client_name)
    AccountWindow(service=service, content_root=content_root).run()


def run_account_sync(*, client_name="Cloth NeXt") -> int:
    """Headless check-in launched by Blender. Exit 0 also when skipped or offline."""
    service = AccountService(client_name=client_name)
    try:
        with service.operation():
            service.sync_if_due()
    except ServiceError as exc:
        LOG.info("PolyWorks sync finished: %s", exc.code)
    except StorageError:
        LOG.warning("PolyWorks sync could not use local account storage")
        return 3
    return 0


# Background tray -----------------------------------------------------------------------------
OPEN_REQUEST_FILE = "companion-open.request"  # Blender asks a running tray to open the window
TRAY_POLL_MS = 400


class AccountTray:
    """Background Companion: licence check-ins on a worker thread, a tray icon,
    and the account window on demand. No console and no window until asked.

    Exits with its Blender (``blender_pid``) or from the tray menu.
    """

    def __init__(self, *, service: AccountService, content_root: Path | None,
                 blender_pid: int = 0):
        from companion import tray
        from companion.app import _asset, _windows_identity
        _windows_identity()
        self.tray_module = tray
        self.service = service
        self.content_root = content_root
        self.blender_pid = blender_pid
        self.root = tk.Tk()
        self.root.withdraw()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="polyworks-sync")
        self.events: queue.Queue = queue.Queue()
        self.window: AccountWindow | None = None
        self.closed = False
        self.icon = tray.TrayIcon(
            str(_asset("cloth_next.ico")), "Cloth NeXt Companion · PolyWorks Account",
            on_open=lambda: self.events.put("open"), on_quit=lambda: self.events.put("quit")).start()
        if self.icon.error:
            LOG.warning("tray icon unavailable: %s", self.icon.error)
        self._heartbeat()
        self.root.after(10_000, self._sync)
        self.root.after(TRAY_POLL_MS, self._poll)

    @staticmethod
    def other_tray_running(store) -> bool:
        from cloth_next.polyworks.status import presence_running
        from companion import tray
        try:
            presence = store.read_json(PRESENCE_FILE)
        except (OSError, ValueError, StorageError):
            return False
        return bool(isinstance(presence, dict) and presence.get("mode") == "tray"
                    and presence.get("pid") != os.getpid()
                    and presence_running(presence, time.time())
                    and tray.process_alive(int(presence.get("pid") or 0)))

    def _heartbeat(self):
        if self.closed:
            return
        try:
            self.service.store.write_json(PRESENCE_FILE, {
                "pid": os.getpid(), "mode": "tray", "heartbeat_at": time.time()})
        except OSError:
            pass
        self.root.after(HEARTBEAT_MS, self._heartbeat)

    def _sync(self):
        """The licence check-in, off the Tk thread."""
        if self.closed:
            return

        def check():
            try:
                with self.service.operation():
                    self.service.sync_if_due()
            except ServiceError as exc:
                LOG.info("PolyWorks sync finished: %s", exc.code)
            except StorageError:
                LOG.warning("PolyWorks sync could not use local account storage")
        if self.window is None or self.window.closed:  # an open window checks in itself
            self.pool.submit(check)
        self.root.after(SYNC_CHECK_MS, self._sync)

    def open_window(self):
        if self.window is not None and not self.window.closed:
            window = self.window.window
            window.deiconify()
            window.lift()
            window.focus_force()
            return
        self.window = AccountWindow(self.root, service=self.service,
                                    content_root=self.content_root, owns_presence=False)

    def _poll(self):
        if self.closed:
            return
        request = self.service.store.path(OPEN_REQUEST_FILE)
        try:
            if request.exists():
                request.unlink()
                self.events.put("open")
        except OSError:
            pass
        if self.blender_pid and not self.tray_module.process_alive(self.blender_pid):
            self.events.put("quit")
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "open":
                self.open_window()
            elif event == "quit":
                return self.close()
        self.root.after(TRAY_POLL_MS, self._poll)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.window is not None:
            self.window.close()
        self.icon.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        try:
            presence = self.service.store.read_json(PRESENCE_FILE)
            if isinstance(presence, dict) and presence.get("pid") == os.getpid():
                self.service.store.path(PRESENCE_FILE).unlink(missing_ok=True)
        except (OSError, ValueError, StorageError):
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def run_account_tray(content_root: Path | None = None, *, client_name="Cloth NeXt",
                     blender_pid: int = 0, open_window: bool = False) -> int:
    """Resident background Companion (Windows tray). Only one runs per account folder."""
    service = AccountService(client_name=client_name)
    if AccountTray.other_tray_running(service.store):
        if open_window:
            try:
                service.store.path(OPEN_REQUEST_FILE).write_text("open", encoding="utf-8")
            except OSError:
                pass
        return 0
    tray = AccountTray(service=service, content_root=content_root, blender_pid=blender_pid)
    if open_window:
        tray.events.put("open")
    tray.run()
    return 0
