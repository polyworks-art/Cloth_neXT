"""Compact dark Cloth NeXt Bake progress companion."""
from __future__ import annotations
import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import math
import random
import re
import sys
import tkinter as tk
import time
import traceback
import webbrowser
from dataclasses import replace
from tkinter import ttk

from cloth_next.bake.status import (ACTIVITY_LABELS, BakeActivity, BakeJobKind,
                                    BakeSnapshot, BakeState, format_duration)
from cloth_next.bake.transport import DemoTransport, LocalSocketClient
from companion.particle_motion import advance_particle, smooth_rate
from companion.performance_graph import FramePerformanceHistory

COMPANION_MESSAGE_BATCH_LIMIT=2048


def receive_message_batch(transport,*,limit=COMPANION_MESSAGE_BATCH_LIMIT):
    """Drain buffered status traffic so it cannot delay control messages."""
    messages=[]
    first=transport.receive(.01)
    if first is None:return messages
    messages.append(first)
    while len(messages)<limit:
        message=transport.receive(0.0)
        if message is None:break
        messages.append(message)
    return messages

BG="#303030"; PANEL="#252525"; BORDER="#555555"; TEXT="#f0f0f0"
MUTED="#b8b8b8"; AMBER="#d99a32"; BUTTON="#444444"
GRAPH="#54efc3"; GRAPH_FILL="#24483f"; GRID="#343a39"; ERROR="#ff5964"
ABOUT_TOOLTIP="SideFX, please don’t sue me."
ERROR_DOCS_BASE="https://polyworks-art.github.io/Cloth_neXT/errors/"
COMPACT_HEIGHT=118; DETAILS_HEIGHT=232

def _logger():
    root=Path(os.environ.get("LOCALAPPDATA",Path.home()))/"Cloth NeXt"/"logs"
    logger=logging.getLogger("cloth_next_companion")
    if not logger.handlers:
        try:
            root.mkdir(parents=True,exist_ok=True)
            handler=RotatingFileHandler(root/"companion.log",maxBytes=256*1024,
                                        backupCount=1,encoding="utf-8")
        except OSError:
            handler=logging.NullHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler); logger.setLevel(logging.INFO)
    return logger

LOG=_logger()


class HoverTooltip:
    """Small non-modal tooltip; never steals focus from the Bake window."""
    def __init__(self, widget, text, delay_ms=350):
        self.widget=widget; self.text=text; self.delay_ms=delay_ms
        self._after=None; self._window=None
        widget.bind("<Enter>",self._schedule,add="+")
        widget.bind("<Leave>",self.hide,add="+")
        widget.bind("<ButtonPress>",self.hide,add="+")
    def _schedule(self,_event=None):
        self.hide()
        self._after=self.widget.after(self.delay_ms,self.show)
    def show(self):
        self._after=None
        if self._window is not None or not self.widget.winfo_exists():return
        window=tk.Toplevel(self.widget); self._window=window
        window.wm_overrideredirect(True); window.attributes("-topmost",True)
        x=self.widget.winfo_rootx(); y=self.widget.winfo_rooty()+self.widget.winfo_height()+5
        window.wm_geometry(f"+{x}+{y}")
        tk.Label(window,text=self.text,bg="#171717",fg=TEXT,
                 relief="solid",borderwidth=1,padx=7,pady=4,
                 font=("Segoe UI",8)).pack()
    def hide(self,_event=None):
        if self._after is not None:
            try:self.widget.after_cancel(self._after)
            except tk.TclError:pass
            self._after=None
        if self._window is not None:
            try:self._window.destroy()
            except tk.TclError:pass
            self._window=None


def error_activity_label(snapshot: BakeSnapshot) -> str:
    return f"ERROR · {snapshot.error_code or 'CNX-E199'}"


def error_docs_url(error_code: str) -> str:
    """Return a safe direct documentation URL for one stable CNX code."""
    code=str(error_code or "").strip().upper()
    return f"{ERROR_DOCS_BASE}#{code}" if re.fullmatch(r"CNX-E\d{3}",code) else ""


def details_meta(snapshot: BakeSnapshot) -> str:
    """Readable diagnostic rows shown by the in-window Details foldout."""
    parts=[]
    if snapshot.active_object_name:
        parts.append(f"Object     {snapshot.active_object_name}")
    mode={"MANAGED_INSTALLATION":"Managed","MANAGED":"Managed",
          "EXTERNAL_INSTALLATION":"External","EXTERNAL_SERVER":"Server"}.get(
              snapshot.solver_mode,snapshot.solver_mode.replace("_"," ").title())
    solver=" · ".join(value for value in (mode,snapshot.solver_version) if value)
    if solver:parts.append(f"Solver     {solver}")
    remaining=format_duration(snapshot.estimated_remaining_seconds,
                              approximate=True)
    if remaining!="Unknown":parts.append(f"ETA        {remaining}")
    if snapshot.error_code:parts.append(f"Error      {snapshot.error_code}")
    return "\n".join(parts) or "No additional Bake details yet."


def details_status(snapshot: BakeSnapshot) -> str:
    """Concise detail text without duplicating the progress-bar frame."""
    lines = [line.strip() for line in (snapshot.error_details or "").splitlines()
             if line.strip()]
    concise = []
    for prefix in ("Stage:", "Blender frame:", "Cause:", "What to do:",
                   "Diagnostic log:"):
        match = next((line for line in lines if line.startswith(prefix)), None)
        if match and match not in concise:
            concise.append(match)
    if concise:
        return "\n".join(concise[:3])
    if snapshot.state is BakeState.SIMULATING:
        return ""
    return snapshot.status_message or "No PPF simulation is running."

def _asset(name: str) -> Path:
    base=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parent))
    packaged=base/"companion_assets"/name
    return packaged if packaged.is_file() else Path(__file__).resolve().parent/"assets"/name

def _windows_identity():
    if sys.platform=="win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Polyworks.ClothNeXt.Bake")
        except (AttributeError,OSError): pass

def _match_windows_title_bar(root):
    if sys.platform!="win32": return
    try:
        root.update_idletasks(); hwnd=ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        color=ctypes.c_int(0x00303030); light=ctypes.c_int(0x00F0F0F0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd,35,ctypes.byref(color),ctypes.sizeof(color))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd,36,ctypes.byref(light),ctypes.sizeof(light))
    except (AttributeError,OSError): pass

PARTICLE_ASSETS=("particle_bake_12.png","particle_cloth_16.png",
    "particle_collider_12.png","particle_collision_16.png",
    "particle_pinning_12.png","particle_solver_16.png",
    "particle_quality_12.png","particle_timer_12.png")

class IconParticleField:
    """Small Houdini-inspired icon flow with smooth path noise."""
    WIDTH=76; HEIGHT=72; FRAME_MS=16; COUNT=9
    def __init__(self,parent,reduced_motion=False):
        self.canvas=tk.Canvas(parent,width=self.WIDTH,height=self.HEIGHT,bg=PANEL,highlightthickness=0,borderwidth=0)
        self.reduced_motion=reduced_motion; self._after=None; self._running=False; self._closed=False
        self._rate=.18; self._target=.18; self._last_tick=None; self._images=[]; self._particles=[]
        try:
            self._images=[tk.PhotoImage(file=str(_asset(name))) for name in PARTICLE_ASSETS]
            rng=random.Random()
            for index in range(self.COUNT):
                angle=rng.uniform(0,math.tau)
                particle={"base_x":rng.uniform(0,self.WIDTH),
                    "base_y":rng.uniform(0,self.HEIGHT),
                    "direction_x":math.cos(angle),"direction_y":math.sin(angle),
                    "speed":rng.uniform(10.,24.),"noise_time":0.,
                    "phase":rng.uniform(0,math.tau),
                    "phase_2":rng.uniform(0,math.tau),
                    "frequency":rng.uniform(.5,1.2),
                    "frequency_2":rng.uniform(1.1,2.),
                    "amplitude":rng.uniform(1.4,4.)}
                particle["item"]=self.canvas.create_image(
                    particle["base_x"],particle["base_y"],
                    image=self._images[index%len(self._images)])
                self._particles.append(particle)
            self.available=True
        except Exception:
            LOG.warning("Icon particle field unavailable; using empty fallback.")
            self.available=False; self.canvas.delete("all"); self._images=[]; self._particles=[]
    def start(self):
        if self._running or self._closed or not self.available:return
        self._running=True; self._last_tick=time.perf_counter(); self._tick()
    def set_state(self,state,activity=BakeActivity.IDLE):
        self._target={BakeState.IDLE:.16,BakeState.PREPARING:.52,BakeState.SIMULATING:1.,
            BakeState.FETCHING:.42,BakeState.IMPORTING:.30,BakeState.FINISHED:.24,
            BakeState.CANCELLED:.05,BakeState.ERROR:.08}.get(state,.58)
        if activity is BakeActivity.WRITING_FRAME:self._target=.82
        elif activity in {BakeActivity.BUILDING_PC2,BakeActivity.APPLYING_PLAYBACK}:self._target=.24
    def _tick(self):
        if self._closed or not self._running:return
        now=time.perf_counter()
        elapsed=max(0.,min(now-(self._last_tick or now),.1)); self._last_tick=now
        self._rate=smooth_rate(self._rate,self._target,elapsed)
        if not self.reduced_motion:
            for particle in self._particles:
                x,y=advance_particle(particle,elapsed,self._rate,self.WIDTH,self.HEIGHT)
                self.canvas.coords(particle["item"],x,y)
        try:self._after=self.canvas.after(self.FRAME_MS,self._tick)
        except tk.TclError:self._running=False
    def close(self):
        if self._closed:return
        self._closed=True; self._running=False
        if self._after is not None:
            try:self.canvas.after_cancel(self._after)
            except tk.TclError:pass
            self._after=None

class BakeWindow:
    def __init__(self,transport=None,root=None):
        _windows_identity(); self.transport=transport or DemoTransport(); self.root=root or tk.Tk()
        # Prevent Tk's default top-left placement from flashing before the
        # preparation window receives its first Bake command.
        self.root.withdraw()
        LOG.info("startup pid=%s tk_initialized=true",os.getpid())
        self.root.title("Cloth NeXt Bake"); self.root.configure(bg=BG); self.root.resizable(False,False)
        self.root.geometry(f"390x{COMPACT_HEIGHT}"); self.root.minsize(390,COMPACT_HEIGHT)
        self._app_icon=tk.PhotoImage(file=str(_asset("cloth_next.png")))
        self.root.iconphoto(True,self._app_icon)
        self.primary=tk.StringVar(value="Ready")
        self.secondary=tk.StringVar(value="No PPF simulation is running.")
        self.progress_text=tk.StringVar(value="Ready")
        self.time_text=tk.StringVar(value="00:00")
        self.remaining_text=tk.StringVar(value="")
        self.details_meta_text=tk.StringVar(value="No additional Bake details yet.")
        self.activity_text=tk.StringVar(value="Waiting for a Bake")
        self._activity_pending=None; self._activity_after=None; self._closed=False
        self._blink_after=None; self._blink_phase=False
        self._progress_fraction=0.0
        self._performance=FramePerformanceHistory()
        self._job_modal=False
        self._error_details=""
        self._last_snapshot=BakeSnapshot()
        self._connection_failed=False
        self._details_visible=False
        self._configure_style(); self._build(); _match_windows_title_bar(self.root)
        self.show(BakeSnapshot()); self.particles.start()
        self.root.update_idletasks()
        self._center_on_screen()
        if os.environ.get("CLOTH_NEXT_COMPANION_TEST_MODE") != "hidden":
            self.root.deiconify()
        self.root.protocol("WM_DELETE_WINDOW",self.close)

    def _center_on_screen(self):
        width=max(390,self.root.winfo_width())
        requested=max(COMPACT_HEIGHT,self.root.winfo_reqheight())
        height=max(DETAILS_HEIGHT,requested) if self._details_visible else requested
        x=max(0,(self.root.winfo_screenwidth()-width)//2)
        y=max(0,(self.root.winfo_screenheight()-height)//2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def enter_bake_mode(self,payload):
        job_id=str(payload.get("job_id", ""))
        try:
            already_visible=bool(
                self.root.winfo_ismapped() and self.root.winfo_viewable())
            self.root.minsize(390,COMPACT_HEIGHT)
            self.root.update_idletasks()
            if self.root.winfo_width()<100 or self.root.winfo_height()<80:
                self.root.geometry(f"390x{COMPACT_HEIGHT}"); self.root.update_idletasks()
            if not already_visible:
                self._center_on_screen()
                self.root.deiconify()
            self.root.update_idletasks()
            if os.environ.get("CLOTH_NEXT_COMPANION_TEST_MODE") == "hidden":
                self.root.withdraw(); self.root.update_idletasks()
            self.root.attributes("-topmost",True); self.root.lift()
            if not already_visible:
                self.root.after_idle(self.root.focus_force)
            self.root.update_idletasks()
            visible=bool(self.root.winfo_ismapped() and self.root.winfo_viewable())
            topmost=bool(self.root.attributes("-topmost"))
            response={"job_id":job_id,"companion_process_id":os.getpid(),
                      "window_created":True,"window_visible":visible,
                      "topmost_applied":topmost,"transport_ready":True}
            LOG.info("enter_bake_mode %s",json.dumps(response,sort_keys=True))
            if visible and topmost:
                self._job_modal=True
                self.transport.send("bake_window_ready",response)
            else:
                self.transport.send("startup_error",{"job_id":job_id,
                    "message":"Bake window did not become visible or topmost."})
        except Exception as exc:
            LOG.exception("enter_bake_mode failed job_id=%s",job_id)
            self.transport.send("startup_error",{"job_id":job_id,
                "message":f"Bake window could not enter foreground mode: {exc}"})

    def _configure_style(self):
        style=ttk.Style(self.root); style.theme_use("clam"); self._style=style
        style.configure("CN.TFrame",background=BG); style.configure("Inset.TFrame",background=PANEL)
        style.configure("CN.TLabel",background=BG,foreground=TEXT,font=("Segoe UI",9))
        style.configure("Muted.TLabel",background=PANEL,foreground=MUTED,font=("Segoe UI",9))
        style.configure("Primary.TLabel",background=PANEL,foreground=TEXT,font=("Segoe UI Semibold",9))
        style.configure("CN.TButton",background=BUTTON,foreground=TEXT,bordercolor=BORDER,padding=(8,3))
        style.map("CN.TButton",background=[("active","#444950"),("disabled",PANEL)],foreground=[("disabled","#6f747a")])

    def _build(self):
        self.root.columnconfigure(0,weight=1); self.root.rowconfigure(0,weight=1)
        outer=ttk.Frame(self.root,style="CN.TFrame",padding=(6,5,6,4)); outer.grid(sticky="nsew")
        outer.columnconfigure(0,weight=1)
        body=ttk.Frame(outer,style="CN.TFrame"); body.grid(row=0,column=0,sticky="ew")
        icon_box=tk.Frame(body,bg=PANEL,highlightbackground=BORDER,highlightthickness=1,width=78,height=74)
        icon_box.grid(row=0,column=0,rowspan=2,sticky="ns",padx=(0,5)); icon_box.grid_propagate(False)
        self.particles=IconParticleField(icon_box,os.environ.get("CLOTH_NEXT_REDUCED_MOTION")=="1")
        self.particles.canvas.place(relx=.5,rely=.5,anchor="center")
        right=ttk.Frame(body,style="CN.TFrame"); right.grid(row=0,column=1,sticky="ew"); body.columnconfigure(1,weight=1)
        self.progress=tk.Canvas(right,width=270,height=22,bg=PANEL,highlightbackground="#777777",highlightthickness=1,borderwidth=0)
        self.progress.grid(row=0,column=0,sticky="ew"); right.columnconfigure(0,weight=1)
        self.progress_fill=self.progress.create_rectangle(0,0,0,22,fill=AMBER,outline="")
        self.progress_label=self.progress.create_text(136,11,text="Ready",fill=TEXT,font=("Segoe UI",8))
        self.progress.bind("<Configure>",self._resize_progress)
        self.performance=tk.Canvas(
            right,width=270,height=44,bg=PANEL,highlightbackground="#777777",
            highlightthickness=1,borderwidth=0)
        self.performance.grid(row=1,column=0,sticky="ew",pady=(5,0))
        self.performance.bind("<Configure>",self._draw_performance)
        self.error_panel=tk.Frame(
            right,bg=PANEL,highlightbackground=ERROR,highlightthickness=1,
            height=44)
        self.error_panel.grid(row=1,column=0,sticky="nsew",pady=(5,0))
        self.error_panel.grid_propagate(False)
        self.inline_error_title=tk.Label(
            self.error_panel,text="",bg=PANEL,fg=ERROR,
            font=("Segoe UI Semibold",8),anchor="w")
        self.inline_error_title.pack(fill="x",padx=7,pady=(4,0))
        error_line=tk.Frame(self.error_panel,bg=PANEL)
        error_line.pack(fill="x",padx=7,pady=(1,3))
        self.inline_error_description=tk.Label(
            error_line,text="",bg=PANEL,fg=MUTED,font=("Segoe UI",7),
            anchor="w")
        self.inline_error_description.pack(side="left",fill="x",expand=True)
        self.inline_error_link=tk.Label(
            error_line,text="Details  ↗",bg=PANEL,fg=GRAPH,
            activebackground=PANEL,activeforeground="#9effdf",
            font=("Segoe UI Semibold",7,"underline"),cursor="hand2",
            takefocus=True)
        self.inline_error_link.pack(side="right",padx=(6,0))
        self.inline_error_link.bind("<Button-1>",self._open_error_docs)
        self.inline_error_link.bind("<Return>",self._open_error_docs)
        self.inline_error_link.bind("<space>",self._open_error_docs)
        self.error_panel.grid_remove()
        self.details_panel=tk.Frame(outer,bg=PANEL,highlightbackground=BORDER,
                                    highlightthickness=1,padx=8,pady=6)
        self.details_panel.grid(row=1,column=0,sticky="ew",pady=(5,0))
        self.details_panel.grid_remove()
        tk.Label(self.details_panel,textvariable=self.primary,bg=PANEL,fg=TEXT,
                 font=("Segoe UI Semibold",9),anchor="w").pack(fill="x")
        tk.Label(self.details_panel,textvariable=self.secondary,bg=PANEL,fg=MUTED,
                 font=("Segoe UI",8),anchor="w",justify="left",
                 wraplength=350).pack(fill="x",pady=(2,0))
        tk.Label(self.details_panel,textvariable=self.details_meta_text,bg=PANEL,
                 fg=MUTED,font=("Segoe UI",8),anchor="w",justify="left",
                 wraplength=350).pack(fill="x",pady=(3,0))
        self.error_docs_link=tk.Label(
            self.details_panel,text="",bg=PANEL,fg="#54efc3",
            activebackground=PANEL,activeforeground="#9effdf",
            font=("Segoe UI Semibold",8,"underline"),anchor="w",
            cursor="hand2",takefocus=True)
        self.error_docs_link.bind("<Button-1>",self._open_error_docs)
        self.error_docs_link.bind("<Return>",self._open_error_docs)
        self.error_docs_link.bind("<space>",self._open_error_docs)
        self._error_docs_url=""
        bottom=ttk.Frame(outer,style="CN.TFrame",height=30); bottom.grid(row=2,column=0,sticky="ew",pady=(5,0))
        self.details_button=ttk.Button(bottom,text="Details",width=8,
            style="CN.TButton",command=self._toggle_details)
        self.details_button.pack(side="left")
        self.eta_label=ttk.Label(
            bottom,textvariable=self.time_text,style="CN.TLabel",
            anchor="center",justify="center")
        self.eta_label.place(relx=.5,rely=.5,anchor="center")
        self.cancel=ttk.Button(bottom,text="Cancel",width=8,style="CN.TButton",command=self._cancel)
        self.cancel.pack(side="right")
        about=ttk.Button(bottom,text="?",width=3,style="CN.TButton")
        about.pack(side="right",padx=(5,5))
        self.about_tooltip=HoverTooltip(about,ABOUT_TOOLTIP)

    def _resize_progress(self,event=None):
        width=max(1,event.width if event is not None else self.progress.winfo_width())
        self.progress.coords(self.progress_fill,0,0,width*self._progress_fraction,22)
        self.progress.coords(self.progress_label,width/2,11)

    def _draw_performance(self,event=None):
        canvas=self.performance
        width=max(2,event.width if event is not None else canvas.winfo_width())
        height=max(2,event.height if event is not None else canvas.winfo_height())
        canvas.delete("all")
        for fraction in (.25,.5,.75):
            y=round(height*fraction)
            canvas.create_line(0,y,width,y,fill=GRID)
        values=tuple(self._performance.scores)
        if not values:
            canvas.create_text(
                7,height/2,text="Collecting frame performance…",fill=MUTED,
                font=("Segoe UI",7),anchor="w")
            return
        usable_width=max(1,width-12); usable_height=max(1,height-12)
        step=usable_width/max(1,len(values)-1)
        points=[]
        for index,value in enumerate(values):
            points.extend((6+index*step,6+usable_height*(1-value/100.0)))
        if len(values)>1:
            area=[points[0],height-5,*points,points[-2],height-5]
            canvas.create_polygon(area,fill=GRAPH_FILL,outline="")
            canvas.create_line(*points,fill=GRAPH,width=2,smooth=True)
        else:
            canvas.create_oval(points[0]-2,points[1]-2,
                               points[0]+2,points[1]+2,fill=GRAPH,outline="")
        canvas.create_text(7,6,text="PERFORMANCE",fill=MUTED,
                           font=("Segoe UI Semibold",6),anchor="nw")
        canvas.create_text(width-7,6,text=str(self._performance.latest),
                           fill=GRAPH,font=("Segoe UI Semibold",7),anchor="ne")

    def _show_error_panel(self,snapshot):
        is_error=snapshot.state is BakeState.ERROR
        if is_error:
            self.performance.grid_remove(); self.error_panel.grid()
            code=snapshot.error_code or "CNX-E199"
            self.inline_error_title.configure(
                text=f"{code}  ·  {snapshot.error_summary or 'Bake failed'}")
            concise=details_status(snapshot).replace("\n","  ·  ")
            self.inline_error_description.configure(
                text=(concise or "Open the error guide for recovery steps.")[:82])
            self.inline_error_link.configure(text=f"Open {code}  ↗")
            self.eta_label.place_forget()
        else:
            self.error_panel.grid_remove(); self.performance.grid()
            if not self.eta_label.winfo_manager():
                self.eta_label.place(relx=.5,rely=.5,anchor="center")

    def _toggle_details(self):
        self._details_visible=not self._details_visible
        if self._details_visible:self.details_panel.grid()
        else:self.details_panel.grid_remove()
        self.details_button.configure(
            text="Hide" if self._details_visible else "Details")
        self.root.update_idletasks()
        width=max(390,self.root.winfo_width())
        requested=max(COMPACT_HEIGHT,self.root.winfo_reqheight())
        height=max(DETAILS_HEIGHT,requested) if self._details_visible else requested
        self.root.geometry(
            f"{width}x{height}+{self.root.winfo_x()}+{self.root.winfo_y()}")
    def _cancel(self):
        self.transport.request_cancel(); self.primary.set("Cancelling…"); self.cancel.state(["disabled"])

    def _open_error_docs(self,_event=None):
        if self._error_docs_url:
            webbrowser.open(self._error_docs_url)

    def _update_error_docs_link(self,error_code):
        self._error_docs_url=error_docs_url(error_code)
        if self._error_docs_url:
            code=str(error_code).strip().upper()
            self.error_docs_link.configure(
                text=f"Open {code} documentation  ↗")
            if not self.error_docs_link.winfo_manager():
                self.error_docs_link.pack(fill="x",pady=(5,0))
        else:
            self.error_docs_link.pack_forget()

    def _set_activity(self,value,immediate=False):
        value=" ".join(str(value).replace("\\","/").split())
        if ":/" in value or "0x" in value:value="Running solver"
        value=value[:52]
        if not value or value==self.progress_text.get():value="Running solver"
        self._activity_pending=value
        if immediate:self.activity_text.set(value); return
        if self._activity_after is None:
            def apply():
                self._activity_after=None
                if not self._closed and self._activity_pending:self.activity_text.set(self._activity_pending)
            self._activity_after=self.root.after(180,apply)

    def _set_error_blink(self,enabled):
        if enabled and self._blink_after is None:
            def tick():
                if self._closed:return
                self._blink_phase=not self._blink_phase
                color="#6f1118" if self._blink_phase else BG
                border="#ff3948" if self._blink_phase else "#777777"
                self.root.configure(bg=color)
                self._style.configure("CN.TFrame",background=color)
                self._style.configure("CN.TLabel",background=color)
                self.error_panel.configure(highlightbackground=border)
                self.progress.configure(highlightbackground=border)
                self._blink_after=self.root.after(380,tick)
            tick()
        elif not enabled:
            if self._blink_after is not None:
                try:self.root.after_cancel(self._blink_after)
                except tk.TclError:pass
                self._blink_after=None
            self._blink_phase=False; self.root.configure(bg=BG)
            self._style.configure("CN.TFrame",background=BG)
            self._style.configure("CN.TLabel",background=BG)
            self.error_panel.configure(bg=PANEL,highlightbackground=ERROR)
            self.progress.configure(highlightbackground="#777777")

    def show(self,snapshot: BakeSnapshot):
        self._last_snapshot=snapshot
        performance_changed=self._performance.observe(snapshot)
        self.root.update_idletasks()
        width=max(1,self.progress.winfo_width()); fraction=snapshot.progress_fraction
        self._progress_fraction=fraction
        self.progress.coords(self.progress_fill,0,0,width*fraction,22)
        modal = (snapshot.job_kind is BakeJobKind.BAKE and snapshot.active
                 and self._job_modal)
        if modal != self._job_modal:
            self._job_modal=modal
            self.root.attributes("-topmost", modal)
            if modal:
                self.root.lift()
                self.root.after_idle(self.root.focus_force)
        if snapshot.current_frame is not None and snapshot.progress_total:
            self.progress_text.set(
                f"Frame {snapshot.current_frame} · {snapshot.progress_current} / "
                f"{snapshot.progress_total}")
        elif snapshot.progress_total:
            self.progress_text.set(f"{snapshot.progress_fraction:.0%}")
        else: self.progress_text.set(snapshot.status_title or "Ready")
        self.progress.itemconfigure(self.progress_label,text=self.progress_text.get())
        self.progress.coords(self.progress_label,width/2,11)
        self.primary.set(snapshot.error_summary or snapshot.status_title or "Ready")
        self._error_details=snapshot.error_details or ""
        self.secondary.set(details_status(snapshot))
        self.details_meta_text.set(details_meta(snapshot))
        self._update_error_docs_link(snapshot.error_code)
        label=snapshot.activity_label or ACTIVITY_LABELS.get(snapshot.activity_code,"Running solver")
        if snapshot.activity_code is BakeActivity.WRITING_FRAME and snapshot.current_frame is not None:label=f"Writing frame {snapshot.current_frame}"
        if snapshot.state is BakeState.ERROR:
            label=error_activity_label(snapshot)
        self._set_activity(label,snapshot.state in {BakeState.ERROR,BakeState.CANCELLING,BakeState.CANCELLED,BakeState.FINISHED})
        self._show_error_panel(snapshot)
        if performance_changed or snapshot.state is not BakeState.ERROR:
            self._draw_performance()
        self._set_error_blink(snapshot.state is BakeState.ERROR)
        self.particles.set_state(snapshot.state,snapshot.activity_code)
        remaining=format_duration(snapshot.estimated_remaining_seconds,approximate=True)
        self.remaining_text.set("" if remaining=="Unknown" else f"remaining {remaining}")
        if snapshot.state is BakeState.FINISHED:
            self.time_text.set(f"Finished in {format_duration(snapshot.elapsed_seconds)}")
        elif snapshot.active:
            self.time_text.set("ETA calculating…" if remaining=="Unknown"
                               else f"ETA  {remaining}")
        else:
            self.time_text.set("Ready")
        self.cancel.state(["!disabled"] if snapshot.can_cancel else ["disabled"])

    def disconnected(self):
        if self._connection_failed:
            return
        if self._last_snapshot.active:
            self.connection_error("CNX-E142",
                                  "The connection to Blender was lost.")
            return
        self._job_modal=False
        self.root.attributes("-topmost",False)
        self.primary.set("Disconnected from Blender"); self.secondary.set("Blender-side work is unaffected.")
        self.cancel.state(["disabled"])

    def connection_error(self,code,message):
        """Keep a failed active window visible until explicit user close."""
        self._connection_failed=True
        previous=self._last_snapshot
        self.show(replace(previous,state=BakeState.ERROR,error_code=code,
            error_summary=message,error_details=(
                "Stage: Companion transport\n"
                f"Cause: {message}\n"
                "What to do: Preserve Blender's bake-errors.log, then retry."),
            status_message=message,can_cancel=False,can_pause=False,
            activity_code=BakeActivity.ERROR,
            activity_detail="Companion transport"))

    def close(self):
        if self._closed:return
        self._closed=True; self.particles.close()
        if self._blink_after is not None:
            try:self.root.after_cancel(self._blink_after)
            except tk.TclError:pass
            self._blink_after=None
        if self._activity_after is not None:
            try:self.root.after_cancel(self._activity_after)
            except tk.TclError:pass
        self.transport.close(); self.root.destroy()
    def run(self):
        def poll():
            try:
                latest_status=None
                for message in receive_message_batch(self.transport):
                    if message["type"]=="bake_status":latest_status=message["snapshot"]
                    elif message["type"]=="session_hello":self.transport.send("ready")
                    elif message["type"]=="enter_bake_mode":self.enter_bake_mode(message["payload"])
                    elif message["type"]=="shutdown":self.close(); return
                if latest_status is not None:self.show(latest_status)
                if getattr(self.transport,"closed",False):self.disconnected()
            except (ValueError,PermissionError):
                self.connection_error("CNX-E116",
                                      "The Companion status protocol failed.")
            except OSError:
                self.disconnected()
            if self.root.winfo_exists(): self.root.after(50,poll)
        self.root.after(10,poll); self.root.mainloop()

def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--port",type=int); parser.add_argument("--token")
    args=parser.parse_args(argv); transport=LocalSocketClient(args.port,args.token) if args.port and args.token else DemoTransport()
    try: BakeWindow(transport).run()
    except Exception:
        LOG.error("uncaught companion exception\n%s",traceback.format_exc())
        raise
if __name__=="__main__": main()
