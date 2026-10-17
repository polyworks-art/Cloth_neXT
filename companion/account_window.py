# SPDX-License-Identifier: GPL-3.0-or-later
"""All editable account UI lives in the Companion, never in Blender RNA."""
from concurrent.futures import ThreadPoolExecutor
import queue
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
import webbrowser

from cloth_next.polyworks.client import AccountClient, AccountError, UNAVAILABLE
from cloth_next.polyworks.storage import LocalStore, StorageError, default_root, account_operation
from cloth_next.polyworks.display import publish_status
from cloth_next.polyworks.entitlements import EntitlementError
from cloth_next.onboarding import default_resource_root
from companion.onboarding_window import (BG, PANEL as CARD, LINE, WHITE as FG,
                                         MUTED, ONBOARDING_FONT as FONT,
                                         _register_onboarding_fonts, InfoWindow)


class WelcomeButton(tk.Canvas):
    """The Welcome screen's rounded monochrome button treatment."""
    def __init__(self, parent, label, command, *, primary=False):
        self.label, self.command, self.primary = label, command, primary
        self.enabled = True
        self.font = tkfont.Font(family=FONT, size=8, weight='bold')
        super().__init__(parent, width=self.font.measure(label)+30, height=29,
                         bg=parent.cget('bg'), highlightthickness=0, bd=0, cursor='hand2')
        self.canvas = self
        self.bind('<Configure>', lambda _:self._paint())
        self.bind('<Enter>', lambda _:self._paint(hover=True))
        self.bind('<Leave>', lambda _:self._paint())
        self.bind('<Button-1>', lambda _:self.command() if self.enabled else None)
        self.bind('<Return>', lambda _:self.command() if self.enabled else None)
        self.configure(takefocus=True)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor='hand2' if enabled else 'arrow', takefocus=enabled)
        self._paint()

    def _paint(self, hover=False):
        self.delete('all')
        width, height = max(self.winfo_width(), self.winfo_reqwidth()), 29
        fill = ('#dededb' if hover else FG) if self.primary and self.enabled else (
            '#202326' if hover and self.enabled else CARD)
        InfoWindow._rounded_rectangle(self, 1, 1, width-1, height-1, 8, fill=fill,
                                       outline=FG if self.primary and self.enabled else LINE, width=1)
        self.create_text(width/2,height/2,text=self.label,font=self.font,
                         fill=(BG if self.primary else FG) if self.enabled else '#666b72')

    def _p(self, value):
        return value


class WelcomeCard(tk.Canvas):
    """Reuse Welcome/What's-New polygon geometry, rather than square Tk frames."""
    FILL = '#101214'

    def __init__(self, parent):
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0, height=100)
        self.canvas = self
        self.body = tk.Frame(self, bg=self.FILL, padx=8, pady=3)
        self.body_id = self.create_window(12, 12, window=self.body, anchor='nw')
        self.body.bind('<Configure>', self._measure)
        self.bind('<Configure>', self._resize)

    def _p(self, value):
        return value

    def _measure(self, _event):
        height = self.body.winfo_reqheight()+24
        if self.winfo_reqheight() != height:
            self.configure(height=height)
        self._paint()

    def _resize(self, event):
        self.itemconfigure(self.body_id, width=max(1, event.width-24))
        self._paint()

    def _paint(self):
        self.delete('card-background')
        width, height = self.winfo_width(), self.body.winfo_reqheight()+24
        shape = InfoWindow._rounded_rectangle(self, 1, 1, width-1, height-1, 8,
            fill=self.FILL, outline=LINE, width=1, tags='card-background')
        self.tag_lower(shape)


class AccountWindow:
    def __init__(self, parent, *, root_path=None, purchase_detected=False, superhive_connected=False,
                 client=None, content_root=None):
        self.window = tk.Toplevel(parent)
        self.content_root = content_root or default_resource_root()
        _register_onboarding_fonts(self.content_root)
        self.window.title('PolyWorks Account — Cloth NeXt')
        self.window.configure(bg=BG)
        try:
            from companion.app import _match_windows_title_bar
            _match_windows_title_bar(self.window, light=False, background_color=0x00000000)
        except (ImportError, tk.TclError):
            pass
        width, height = 600, min(810, self.window.winfo_screenheight()-100)
        x = max(0, (self.window.winfo_screenwidth()-width)//2)
        y = max(0, (self.window.winfo_screenheight()-height)//2-25)
        self.window.geometry(f'{width}x{height}+{x}+{y}')
        self.window.minsize(480, 400)
        footer = tk.Frame(self.window, bg=BG, padx=28, pady=16)
        footer.pack(side='bottom', fill='x')
        tk.Label(footer, text='Cloth NeXt  /  PolyWorks', bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side='left')
        WelcomeButton(footer, 'Sign In Later', self.close, primary=True).pack(side='right')
        canvas = tk.Canvas(self.window, bg=BG, highlightthickness=0)
        style = ttk.Style(self.window)
        # Native Windows scrollbar elements ignore dark background options.
        # Use Clam elements for this named style only; leave the app theme intact.
        for part in ('trough', 'thumb'):
            element = f'PolyWorks.Vertical.Scrollbar.{part}'
            if element not in style.element_names():
                style.element_create(element, 'from', 'clam', f'Vertical.Scrollbar.{part}')
        style.layout('PolyWorks.Vertical.TScrollbar', [
            ('PolyWorks.Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
                ('PolyWorks.Vertical.Scrollbar.thumb', {'sticky': 'nswe', 'expand': True})]})])
        style.configure('PolyWorks.Vertical.TScrollbar', background=LINE, troughcolor=BG,
                        bordercolor=BG, lightcolor=LINE, darkcolor=LINE,
                        borderwidth=0, relief='flat', width=8, gripcount=0)
        style.map('PolyWorks.Vertical.TScrollbar', background=[('pressed', '#737b86'),
                                                              ('active', '#505760')])
        scrollbar = ttk.Scrollbar(self.window, orient='vertical', command=canvas.yview,
                                  style='PolyWorks.Vertical.TScrollbar', takefocus=False)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        self.body = tk.Frame(canvas, bg=BG, padx=28, pady=22)
        body_id = canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event:canvas.itemconfigure(body_id, width=event.width))
        self.window.bind('<MouseWheel>', lambda event:canvas.yview_scroll(-int(event.delta/120), 'units'))
        self.client = client or AccountClient(LocalStore(root_path or default_root()))
        self.purchase_detected = purchase_detected
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='polyworks-account')
        self.events = queue.Queue()
        self.closed = False
        self.busy = False
        self.device_pending = False
        self.buttons = []
        self.status = tk.StringVar(value='Loading saved account status…')
        self.license_text = tk.StringVar(value='Not activated')
        self.message = tk.StringVar(value='')
        self.code = tk.StringVar()
        self.header = tk.Canvas(self.body, height=145, bg=BG, highlightthickness=0)
        self.header.pack(fill='x', pady=(0, 12))
        self._images = []
        try:
            background = tk.PhotoImage(file=str(self.content_root/'assets/destination-background.png'))
            logo = tk.PhotoImage(file=str(self.content_root/'assets/cloth-next-logo-ui-crisp.png'))
            self._images.extend((background, logo))
            self.header.create_image(0, 0, image=background, anchor='nw')
            self.header.create_image(4, 5, image=logo, anchor='nw')
            self.window.iconphoto(True, logo)
        except (OSError, tk.TclError):
            pass
        self.header.create_text(66, 12, text='PolyWorks Account', anchor='nw',
                                fill=FG, font=(FONT, 22, 'bold'))
        self.header.create_text(4, 69, text='Your account for Cloth NeXt 3.0.', anchor='nw',
                                fill=MUTED, font=(FONT, 10))
        self.header.create_text(4, 108, text='Cloud Bakes  ·  Coming Soon', anchor='nw',
                                fill=FG, font=(FONT, 10, 'bold'))

        account = self._card('01', 'Connect your account')
        tk.Label(account, textvariable=self.status, justify='left', anchor='w', wraplength=465,
                 bg=account.cget('bg'), fg=MUTED, font=(FONT, 10)).pack(fill='x', pady=(0, 10))
        actions = tk.Frame(account, bg=account.cget('bg'))
        actions.pack(fill='x')
        self._button('Sign In', self._sign_in, parent=actions, side='left', primary=True)
        self._button('Create PolyWorks Account', lambda: self._browser('register'), parent=actions, side='left')

        purchase = self._card('02', 'Superhive purchase')
        self._label('●  Superhive Connected' if superhive_connected else '○  Superhive not connected',
                    parent=purchase, color='#b9c9c5' if superhive_connected else MUTED)
        self._label('●  Cloth NeXt Purchase Detected' if purchase_detected else '○  No purchase detected locally',
                    parent=purchase, color='#b9c9c5' if purchase_detected else MUTED)
        self._label('Purchase activation requires secure verification.', parent=purchase, color=MUTED, size=9)
        self._button('Automatically Validate Superhive Purchase', self._purchase, parent=purchase)

        activation = self._card('03', 'Activate your license')
        self._label('Have a one-time activation code from PolyWorks?', parent=activation, color=MUTED)
        code_row = tk.Frame(activation, bg=activation.cget('bg'))
        code_row.pack(fill='x', pady=(10, 2))
        self.entry = tk.Entry(code_row, textvariable=self.code, show='•', relief='flat', bd=0,
            bg='#0d1013', fg=FG, disabledbackground='#0d1013', disabledforeground=MUTED,
            insertbackground=FG, font=(FONT, 11), highlightthickness=1,
            highlightbackground=LINE, highlightcolor='#738492')
        self.entry.pack(side='left', fill='x', expand=True, ipady=9, padx=(0, 10))
        self._button('Enter Activation Key', self._activate, parent=code_row, side='right')

        license_card = self._card('04', 'Your license')
        tk.Label(license_card, textvariable=self.license_text, bg=license_card.cget('bg'), fg=MUTED,
                 justify='left', anchor='w', font=(FONT, 10), wraplength=465).pack(fill='x')
        self.devices_text = tk.StringVar(value='Activated devices: not available')
        tk.Label(license_card, textvariable=self.devices_text, bg=license_card.cget('bg'), fg=MUTED,
                 anchor='w', font=(FONT, 9)).pack(fill='x', pady=(5, 9))
        license_actions = tk.Frame(license_card, bg=license_card.cget('bg'))
        license_actions.pack(fill='x')
        self._button('Sync License', lambda: self._submit(self.client.synchronize), parent=license_actions, side='left')
        self._button('Manage Account', lambda: self._browser('manage'), parent=license_actions, side='left')
        self._button('Sign Out', lambda: self._submit(self.client.sign_out), requires_backend=False,
                     parent=license_actions, side='right')
        tk.Label(self.body, textvariable=self.message, justify='left', wraplength=495,
                 bg=BG, fg='#b6b0a1', font=(FONT, 9)).pack(fill='x', pady=(6, 0))
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.after(50, self._poll)
        self._submit(lambda: None)

    def _label(self, text, bold=False, *, parent=None, color=FG, size=10):
        parent = parent or self.body
        tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color, anchor='w',
                 font=(FONT, size, 'bold' if bold else 'normal'),
                 wraplength=490, justify='left').pack(fill='x', pady=3)

    def _card(self, number, title):
        surface = WelcomeCard(self.body)
        surface.pack(fill='x', pady=(0, 12))
        card = surface.body
        tk.Label(card, text=number+'  /  '+title, bg=card.cget('bg'), fg=FG, anchor='w',
                 font=(FONT, 11, 'bold')).pack(fill='x', pady=(0, 9))
        return card

    def _button(self, label, callback, requires_backend=True, *, parent=None, side=None, primary=False):
        button = WelcomeButton(parent or self.body, label, callback, primary=primary)
        if side:
            button.pack(side=side, padx=(0, 8))
        else:
            button.pack(fill='x', pady=(10, 0))
        self.buttons.append((button, requires_backend))

    def _set_controls(self, status):
        ready = status.get('backend_ready', False)
        for button, required in self.buttons:
            enabled = not self.busy and (ready or not required)
            button.set_enabled(enabled)
        self.entry.configure(state='normal' if ready and status.get('signed_in') and not self.busy else 'disabled')
        if not ready:
            self.message.set(UNAVAILABLE)

    def _submit(self, operation):
        if self.busy or self.closed:
            return
        self.busy = True
        for button, _ in self.buttons:
            button.set_enabled(False)
        self.entry.configure(state='disabled')
        def work():
            message = ''
            value = None
            try:
                with account_operation(self.client.store.root):
                    value = operation()
                if isinstance(value, str):
                    message = value
                elif hasattr(value, 'tier'):
                    message = f'License Activated — {value.tier}'
            except (AccountError, StorageError, EntitlementError, OSError) as exc:
                message = str(exc) if not isinstance(exc, OSError) else 'Could not access local account storage.'
                value = exc
            except Exception:
                message = 'Account operation failed. Your saved license is preserved.'
            try:
                status = self.client.status()
                devices = None
                if status.get('backend_ready') and status.get('signed_in') and self.client.contract.devices_supported:
                    try:
                        with account_operation(self.client.store.root):
                            devices = self.client.devices()
                    except (AccountError, StorageError):
                        pass
                public = publish_status(self.client.store, self.client, devices=devices)
                public.update(backend_ready=status.get('backend_ready'))
            except (StorageError, OSError, ValueError):
                public = dict(signed_in=False, email='', tier='Unavailable', verification='Local storage unavailable')
            self.events.put((value, message, public))
        self.pool.submit(work)

    def _poll(self):
        if self.closed:
            return
        try:
            value, message, status = self.events.get_nowait()
            self.busy = False
            self.message.set(message)
            self.status.set(('● Signed in' if status.get('signed_in') else '○ Signed out') +
                            ('  ·  '+status['email'] if status.get('email') else (
                                '\nSign in securely in your browser.' if status.get('backend_ready')
                                else '\nPolyWorks accounts — Coming Soon')))
            self.license_text.set(status.get('tier', 'Not activated')+'  ·  '+status.get('verification', ''))
            devices = status.get('devices')
            self.devices_text.set(f'Activated devices: {len(devices)}' if devices is not None
                                  else 'Activated devices: not available')
            self._set_controls(status)
            from cloth_next.polyworks.client import DeviceAuthorization
            if isinstance(value, DeviceAuthorization):
                self.device_pending = True
                self.message.set(f'In your browser, enter: {value.user_code}')
                webbrowser.open(value.verification_uri, new=2)
            elif self.device_pending and isinstance(value, AccountError) and value.code in ('authorization_pending', 'slow_down', 'network_unavailable'):
                device = self.client._device
                if device:
                    self.message.set(f'{message}\nBrowser code: {device.user_code}')
            elif self.device_pending:
                self.device_pending = False
            if self.device_pending:
                device = self.client._device
                if device:
                    delay = max(1000, int((device.next_poll-self.client.monotonic())*1000))
                    self.window.after(delay, lambda: self._submit(self.client.poll_sign_in) if not self.closed and self.device_pending else None)
        except queue.Empty:
            pass
        self.window.after(80, self._poll)

    def _browser(self, action):
        try:
            webbrowser.open(self.client.contract.browser_url(action), new=2)
        except AccountError as exc:
            self.message.set(str(exc))

    def _sign_in(self):
        self._submit(self.client.begin_sign_in)

    def _purchase(self):
        self._submit(lambda: self.client.verify_purchase(self.purchase_detected))

    def _activate(self):
        code = self.code.get()
        self.code.set('')
        self._submit(lambda: self.client.activate(code))

    def close(self):
        self.closed = True
        self.device_pending = False
        self.client.cancel_sign_in()
        # No access to Blender, solver processes or bake transport.
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.window.destroy()
