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
import threading
import time
import traceback
import webbrowser
from dataclasses import replace
from tkinter import font as tkfont, ttk

from cloth_next.bake.status import (ACTIVITY_LABELS, BakeActivity, BakeJobKind,
                                    BakeSnapshot, BakeState, format_duration)
from cloth_next.bake.transport import DemoTransport, LocalSocketClient
from cloth_next.veyra.artifacts import SessionArtifacts
from cloth_next.veyra.model import (CompanionMode, RepairArtifact, VeyraStep,
                                    VEYRA_STEP_LABELS)
from cloth_next.veyra.solver import VeyraCancelled, solve_repair_plan
from companion.particle_motion import (advance_particle,
                                       advance_veyra_particle, smooth_rate)
from companion.frame_progress import CurrentFrameProgressEstimator, FrameProgress
from companion.error_guidance import ErrorGuidanceClient, replace_recommendation

COMPANION_MESSAGE_BATCH_LIMIT=2048


def receive_message_batch(transport,*,limit=COMPANION_MESSAGE_BATCH_LIMIT):
    """Drain buffered status traffic without blocking Tk's animation loop."""
    messages=[]
    first=transport.receive(0.0)
    if first is None:return messages
    messages.append(first)
    while len(messages)<limit:
        message=transport.receive(0.0)
        if message is None:break
        messages.append(message)
    return messages

BG="#303030"; PANEL="#252525"; BORDER="#555555"; TEXT="#f0f0f0"
MUTED="#b8b8b8"; AMBER="#d99a32"; BUTTON="#444444"
SECTION_HEADER="#353535"; SECTION_BODY="#2d2d2d"; VALUE_BG="#242424"
GRAPH=AMBER; GRAPH_FILL="#4b3b25"; GRID="#3b3b3b"; ERROR="#ff5964"
FRAME_FILL="#4b3b25"
ABOUT_TOOLTIP="SideFX, please don’t sue me."
ERROR_DOCS_BASE="https://polyworks-art.github.io/Cloth_neXT/errors/"
COMPACT_HEIGHT=118; DETAILS_HEIGHT=350

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
    if snapshot.error_code:parts.append(f"Error      {snapshot.error_code}")
    return "\n".join(parts) or "No additional Bake details yet."


def details_status(snapshot: BakeSnapshot) -> str:
    """Concise detail text without duplicating the progress-bar frame."""
    lines = [line.strip() for line in (snapshot.error_details or "").splitlines()
             if line.strip()]
    concise = []
    # Recovery is more useful in the compact panel than repeating the complete
    # solver cause. The documentation link retains all deeper context.
    for prefix in ("Stage:", "Blender frame:", "What to do:", "Cause:",
                   "Diagnostic log:"):
        match = next((line for line in lines if line.startswith(prefix)), None)
        if match and match not in concise:
            concise.append(match)
    if concise:
        return "\n".join(concise[:3])
    if snapshot.state is BakeState.SIMULATING:
        return ""
    return snapshot.status_message or "No PPF simulation is running."


def run_stats(snapshot: BakeSnapshot) -> tuple[tuple[str, str], ...]:
    """Stable Houdini-style facts for the current run, never historical graphs."""
    frame = (str(snapshot.current_frame) if snapshot.current_frame is not None
             else "—")
    total = str(snapshot.progress_total) if snapshot.progress_total else "—"
    progress = (f"{snapshot.progress_fraction:.0%}"
                if snapshot.progress_total else "—")
    activity = snapshot.activity_label or ACTIVITY_LABELS.get(
        snapshot.activity_code, "—")
    solver_values = dict(BakeWindow._solver_status_values(None, activity))
    solver = " · ".join(value for value in (
        snapshot.solver_mode.replace("_", " ").title(),
        snapshot.solver_version) if value) or "—"
    if snapshot.companion_mode is CompanionMode.VEYRA:
        step = (VEYRA_STEP_LABELS.get(snapshot.veyra_step, "—")
                if snapshot.veyra_step else "—")
        within = (f"{snapshot.veyra_step_current} / {snapshot.veyra_step_total}"
                  if snapshot.veyra_step_total else "—")
        return (
            ("FRAME", "Veyra"), ("PROGRESS", within),
            ("ELAPSED", format_duration(snapshot.elapsed_seconds)),
            ("SOLVER", "Veyra"), ("CONTACTS", solver_values.get("contacts", "—")),
            ("NEWTON", solver_values.get("newton", "—")),
            ("LINEAR ITERS", solver_values.get("iterations", "—")),
            ("ACTIVITY", step[:34]),)
    return (
        ("FRAME", f"{frame} / {total}"),
        ("PROGRESS", progress),
        ("ELAPSED", format_duration(snapshot.elapsed_seconds)),
        ("SOLVER", solver),
        ("CONTACTS", solver_values.get("contacts", "—")),
        ("NEWTON", solver_values.get("newton", "—")),
        ("LINEAR ITERS", solver_values.get("iterations", "—")),
        ("ACTIVITY", activity[:34] or "—"),
    )

def progress_display_text(snapshot: BakeSnapshot) -> str:
    """Render frame text only when the snapshot carries a real frame."""
    if snapshot.current_frame is not None and snapshot.progress_total:
        return (f"Frame {snapshot.current_frame} · "
                f"{snapshot.progress_current} / {snapshot.progress_total}")
    if snapshot.progress_total:
        return f"{snapshot.progress_fraction:.0%}"
    return snapshot.status_title or "Ready"

def _asset(name: str) -> Path:
    base=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parent))
    packaged=base/"companion_assets"/name
    return packaged if packaged.is_file() else Path(__file__).resolve().parent/"assets"/name

def _windows_identity():
    if sys.platform=="win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            # Preserve the established taskbar/icon identity. It is one shared
            # application identity even while the explicit system mode changes.
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

def _set_bake_window_topmost(root, enabled):
    """Set passive bake-window Z order without repeatedly stealing focus."""
    root.attributes("-topmost",bool(enabled))
    if sys.platform!="win32":return
    try:
        root.update_idletasks()
        hwnd=ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        insert_after=-1 if enabled else -2  # HWND_TOPMOST / HWND_NOTOPMOST
        # Preserve position/size and do not activate the window. This makes the
        # Windows Z-order explicit while leaving Alt-Tab and other apps usable.
        ctypes.windll.user32.SetWindowPos(
            hwnd,insert_after,0,0,0,0,0x0001|0x0002|0x0010)
    except (AttributeError,OSError):pass

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
        self.mode=CompanionMode.BAKE
        self._veyra_seam=(
            self.canvas.create_line(
                self.WIDTH/2,7,self.WIDTH/2,self.HEIGHT-7,
                fill=GRAPH_FILL,width=3,state="hidden") ,
            self.canvas.create_line(
                self.WIDTH/2,7,self.WIDTH/2,self.HEIGHT-7,
                fill=AMBER,width=1,dash=(2,3),state="hidden"))
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
                side=-1 if index%2==0 else 1
                particle.update(start_x=(8.0 if side<0 else self.WIDTH-8.0),
                    start_y=8.0+(index*7.0)%(self.HEIGHT-16.0),
                    seam_offset=side*(1.5+(index%3)),vertical_arc=side*(index%4-1.5),
                    duration=1.8+(index%4)*.25,veyra_time=(index/self.COUNT)*2.2)
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
    def set_mode(self,mode):
        self.mode=CompanionMode(mode)
        seam_state="normal" if self.mode is CompanionMode.VEYRA else "hidden"
        for item in self._veyra_seam:
            self.canvas.itemconfigure(item,state=seam_state)
            self.canvas.tag_lower(item)
        if self.mode is CompanionMode.VEYRA and self.reduced_motion:
            for index,particle in enumerate(self._particles):
                self.canvas.coords(particle["item"],
                    self.WIDTH/2+(-1 if index%2==0 else 1)*(2+index%3),
                    8+(index*7)%(self.HEIGHT-16))
    def _tick(self):
        if self._closed or not self._running:return
        now=time.perf_counter()
        elapsed=max(0.,min(now-(self._last_tick or now),.1)); self._last_tick=now
        self._rate=smooth_rate(self._rate,self._target,elapsed)
        if not self.reduced_motion:
            for particle in self._particles:
                if self.mode is CompanionMode.VEYRA:
                    x,y=advance_veyra_particle(
                        particle,elapsed,self._rate,self.WIDTH,self.HEIGHT)
                else:
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
    def __init__(self,transport=None,root=None,session_root=None,
                 initial_mode=CompanionMode.BAKE):
        _windows_identity(); self.transport=transport or DemoTransport(); self.root=root or tk.Tk()
        initial_mode=CompanionMode(initial_mode)
        # Prevent Tk's default top-left placement from flashing before the
        # preparation window receives its first Bake command.
        self.root.withdraw()
        LOG.info("startup pid=%s tk_initialized=true",os.getpid())
        self.root.title("Cloth NeXt Veyra" if initial_mode is CompanionMode.VEYRA
                        else "Cloth NeXt Bake")
        self.root.configure(bg=BG); self.root.resizable(False,False)
        self.root.geometry(f"390x{COMPACT_HEIGHT}"); self.root.minsize(390,COMPACT_HEIGHT)
        self._app_icon=tk.PhotoImage(file=str(_asset("cloth_next.png")))
        self._veyra_icon=tk.PhotoImage(file=str(_asset("veyra.png")))
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
        self._frame_progress=CurrentFrameProgressEstimator()
        self._frame_progress_state=self._frame_progress.tick()
        self._status_fill_after=None
        self._status_fill_phase=0.0
        self._error_guidance=ErrorGuidanceClient()
        self._guidance_code=""
        self._job_modal=False
        self._error_details=""
        self._last_snapshot=BakeSnapshot()
        self._connection_failed=False
        self._mode=initial_mode
        self._session_artifacts=(SessionArtifacts(session_root)
                                 if session_root else None)
        self._veyra_thread=None; self._veyra_cancel=threading.Event()
        self._veyra_job_id=""
        self._details_visible=False
        self._configure_style(); self._build(); _match_windows_title_bar(self.root)
        initial_snapshot=(BakeSnapshot(
            state=BakeState.STARTING_RUN,job_kind=BakeJobKind.VEYRA,
            companion_mode=CompanionMode.VEYRA,
            veyra_step=VeyraStep.ANALYZING_DIAGNOSTICS,
            veyra_step_index=1,status_title="Analyzing Diagnostics",
            activity_label="Preparing repair analysis",can_cancel=True)
            if initial_mode is CompanionMode.VEYRA else BakeSnapshot())
        self.show(initial_snapshot); self.particles.start()
        self._tick_status_fill()
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
        try: mode=CompanionMode(payload.get("mode", "BAKE"))
        except (TypeError,ValueError): mode=CompanionMode.BAKE
        try:
            self._mode=mode
            self.root.title("Cloth NeXt Veyra" if mode is CompanionMode.VEYRA
                            else "Cloth NeXt Bake")
            self.root.iconphoto(
                True,self._veyra_icon if mode is CompanionMode.VEYRA
                else self._app_icon)
            self.particles.set_mode(mode)
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
            _set_bake_window_topmost(self.root,True); self.root.lift()
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
                if mode is CompanionMode.VEYRA:
                    self._start_veyra(job_id,payload.get("input_artifact"))
            else:
                self.transport.send("startup_error",{"job_id":job_id,
                    "message":"Bake window did not become visible or topmost."})
        except Exception as exc:
            LOG.exception("enter_bake_mode failed job_id=%s",job_id)
            self.transport.send("startup_error",{"job_id":job_id,
                "message":f"Bake window could not enter foreground mode: {exc}"})

    def _start_veyra(self,job_id,artifact_value):
        if self._session_artifacts is None:
            self.transport.send("veyra_error",{"job_id":job_id,
                "message":"VEYRA session artifact root is unavailable."})
            return
        if self._veyra_thread is not None and self._veyra_thread.is_alive():
            self.transport.send("veyra_error",{"job_id":job_id,
                "message":"A VEYRA repair job is already running."})
            return
        self._veyra_cancel=threading.Event()
        self._veyra_job_id=job_id
        started=time.monotonic(); last_sent=[0.0,None]
        def report(step,current,total,detail):
            now=time.monotonic(); final=bool(total and current>=total)
            marker=(step.value,current,total,detail)
            if not final and marker==last_sent[1]:return
            if not final and now-last_sent[0]<.06:return
            last_sent[:]=[now,marker]
            self.transport.send("veyra_progress",{
                "job_id":job_id,"step":step.value,"current":current,
                "total":total,"detail":detail,"elapsed":now-started})
        def work():
            try:
                artifact=RepairArtifact.from_dict(artifact_value)
                value=self._session_artifacts.read_json(
                    artifact,schema="cnx.veyra.input.v1",job_id=job_id)
                plan=solve_repair_plan(value,progress=report,
                                       cancelled=self._veyra_cancel.is_set)
                output=self._session_artifacts.write_json(
                    schema="cnx.veyra.plan.v1",job_id=job_id,
                    name=f"{job_id}.plan.json",value=plan.to_dict())
                self.transport.send("veyra_result",{
                    "job_id":job_id,"artifact":output.to_dict(),
                    "elapsed":time.monotonic()-started})
            except VeyraCancelled:
                self.transport.send("veyra_cancelled",{"job_id":job_id})
            except Exception as exc:
                LOG.exception("VEYRA planning failed job_id=%s",job_id)
                self.transport.send("veyra_error",{
                    "job_id":job_id,"message":str(exc)[:2048]})
        self._veyra_thread=threading.Thread(
            target=work,name="ClothNeXtVeyraWorker",daemon=True)
        self._veyra_thread.start()

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
        outer.columnconfigure(0,weight=1); outer.rowconfigure(1,weight=1)
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
        self.status=tk.Canvas(
            right,height=20,bg=PANEL,highlightbackground="#777777",
            highlightthickness=1,borderwidth=0)
        self.status.grid(row=1,column=0,sticky="ew",pady=(5,0))
        self.status_fill=self.status.create_rectangle(
            0,0,0,20,fill=FRAME_FILL,outline="",tags=("fill",))
        try:
            self._solver_stat_icons={
                "contacts":tk.PhotoImage(
                    file=str(_asset("status_contacts_16.png"))),
                "newton":tk.PhotoImage(
                    file=str(_asset("status_newton_16.png"))),
                "iterations":tk.PhotoImage(
                    file=str(_asset("status_iterations_16.png")))}
        except tk.TclError:
            self._solver_stat_icons={}
        self._status_font=tkfont.Font(family="Segoe UI",size=8)
        self.status.bind("<Configure>",self._resize_status)
        self.status_tooltip=HoverTooltip(
            self.status,"Contacts · Newton steps · Linear iterations")
        self.details_panel=tk.Frame(outer,bg=PANEL,highlightbackground=BORDER,
                                    highlightthickness=1,padx=8,pady=6)
        self.details_panel.grid(row=1,column=0,sticky="nsew",pady=(5,0))
        self.details_panel.grid_remove()
        self.diagnostics_section=tk.Frame(self.details_panel,bg=PANEL)
        tk.Label(self.diagnostics_section,textvariable=self.primary,bg=PANEL,
                 fg=TEXT,font=("Segoe UI Semibold",9),anchor="w").pack(fill="x")
        tk.Label(self.diagnostics_section,textvariable=self.secondary,bg=PANEL,fg=MUTED,
                 font=("Segoe UI",8),anchor="w",justify="left",
                 wraplength=350).pack(fill="x",pady=(2,0))
        tk.Label(self.diagnostics_section,textvariable=self.details_meta_text,bg=PANEL,
                 fg=MUTED,font=("Segoe UI",8),anchor="w",justify="left",
                 wraplength=350).pack(fill="x",pady=(3,0))
        self.error_docs_link=tk.Label(
            self.diagnostics_section,text="",bg=PANEL,fg=AMBER,
            activebackground=PANEL,activeforeground="#efbd69",
            font=("Segoe UI Semibold",8,"underline"),anchor="w",
            cursor="hand2",takefocus=True)
        self.error_docs_link.bind("<Button-1>",self._open_error_docs)
        self.error_docs_link.bind("<Return>",self._open_error_docs)
        self.error_docs_link.bind("<space>",self._open_error_docs)
        self._error_docs_url=""
        self.run_stats_section=tk.Frame(self.details_panel,bg=PANEL)
        self.run_stats_section.pack(fill="both",expand=True)
        self.run_stat_vars={}

        def stat_group(title,rows):
            group=tk.Frame(self.run_stats_section,bg=SECTION_BODY,
                           highlightbackground="#202020",highlightthickness=1)
            group.pack(fill="x",pady=(0,5))
            tk.Label(group,text=f"⌄  {title}",bg=SECTION_HEADER,fg=MUTED,
                     font=("Segoe UI",8),anchor="w",padx=8,pady=4).pack(fill="x")
            body=tk.Frame(group,bg=SECTION_BODY,padx=9,pady=5)
            body.pack(fill="x")
            body.columnconfigure(1,weight=1)
            for row,(key,label) in enumerate(rows):
                tk.Label(body,text=label,bg=SECTION_BODY,fg=MUTED,
                         font=("Segoe UI",8),anchor="e",width=13).grid(
                             row=row,column=0,sticky="e",padx=(0,8),pady=2)
                value=tk.StringVar(value="—"); self.run_stat_vars[key]=value
                tk.Label(body,textvariable=value,bg=VALUE_BG,fg=TEXT,
                         font=("Consolas",8),anchor="w",padx=6,pady=2).grid(
                             row=row,column=1,sticky="ew",pady=2)
            return group

        stat_group("Run",(("FRAME","Frame"),("PROGRESS","Progress"),
                          ("ELAPSED","Elapsed"),("ACTIVITY","Activity")))
        stat_group("Solver",(("SOLVER","Backend"),("CONTACTS","Contacts"),
                             ("NEWTON","Newton Steps"),
                             ("LINEAR ITERS","Linear Iterations")))
        self.run_eta=tk.Label(
            self.run_stats_section,textvariable=self.remaining_text,
            bg=SECTION_HEADER,fg="#9bc46a",font=("Segoe UI Semibold",8),
            anchor="center",pady=4)
        self.run_eta.pack(fill="x",pady=(6,0))
        bottom=ttk.Frame(outer,style="CN.TFrame",height=30); bottom.grid(row=2,column=0,sticky="ew",pady=(5,0))
        self.details_button=ttk.Button(bottom,text="Details",width=8,
            style="CN.TButton",command=self._toggle_details)
        self.details_button.pack(side="left")
        ttk.Label(bottom,textvariable=self.time_text,style="CN.TLabel",
                  anchor="center",justify="center").place(
                      relx=.5,rely=.5,anchor="center")
        self.cancel=ttk.Button(bottom,text="Cancel",width=8,style="CN.TButton",command=self._cancel)
        self.cancel.pack(side="right")
        about=ttk.Button(bottom,text="?",width=3,style="CN.TButton")
        about.pack(side="right",padx=(5,5))
        self.about_tooltip=HoverTooltip(about,ABOUT_TOOLTIP)

    def _resize_progress(self,event=None):
        width=max(1,event.width if event is not None else self.progress.winfo_width())
        self.progress.coords(self.progress_fill,0,0,width*self._progress_fraction,22)
        self.progress.coords(self.progress_label,width/2,11)

    def _resize_status(self,event=None):
        self._draw_status_fill(
            event.width if event is not None else None)
        self._draw_status_content(
            event.width if event is not None else None)

    def _solver_status_values(self,value):
        if not str(value).startswith("Solver"):
            return ()
        patterns=(
            ("contacts",r"([\d,]+)\s+contacts\b"),
            ("newton",r"\bNewton\s+([\d,]+)\b"),
            ("iterations",r"([\d,]+)\s+linear iterations\b"))
        result=[]
        for name,pattern in patterns:
            match=re.search(pattern,value,re.IGNORECASE)
            if match:result.append((name,match.group(1)))
        return tuple(result)

    def _draw_status_content(self,width=None):
        canvas=self.status
        width=max(1,width or canvas.winfo_width())
        height=max(1,canvas.winfo_height())
        canvas.delete("content")
        value=self.activity_text.get()
        values=self._solver_status_values(value)
        if not values or not self._solver_stat_icons:
            canvas.create_text(
                width/2,height/2,text=value,fill=TEXT,
                font=self._status_font,tags=("content",))
            return
        entries=[]
        total=0
        for name,number in values:
            image=self._solver_stat_icons[name]
            entry_width=image.width()+3+self._status_font.measure(number)
            entries.append((name,number,image,entry_width))
            total+=entry_width
        total+=10*max(0,len(entries)-1)
        x=(width-total)/2
        for name,number,image,entry_width in entries:
            canvas.create_image(
                x+image.width()/2,height/2,image=image,
                tags=("content",f"stat-{name}"))
            x+=image.width()+3
            canvas.create_text(
                x,height/2,text=number,fill=TEXT,font=self._status_font,
                anchor="w",tags=("content",f"stat-{name}"))
            x+=self._status_font.measure(number)+10

    def _draw_status_fill(self,width=None):
        width=max(1,width or self.status.winfo_width())
        height=max(1,self.status.winfo_height())
        state=self._frame_progress_state
        if state.indeterminate:
            pulse=max(24.0,width*.28)
            travel=width+pulse
            left=(self._status_fill_phase*travel)-pulse
            self.status.coords(
                self.status_fill,max(0,left),0,min(width,left+pulse),height)
        else:
            self.status.coords(
                self.status_fill,0,0,width*state.fraction,height)

    def _tick_status_fill(self):
        if self._closed:return
        if self._mode is not CompanionMode.VEYRA:
            self._frame_progress_state=self._frame_progress.tick()
        self._status_fill_phase=(
            self._status_fill_phase+0.055)%1.0
        self._draw_status_fill()
        try:self._status_fill_after=self.root.after(
            80,self._tick_status_fill)
        except tk.TclError:self._status_fill_after=None

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
        average=self._performance.average_frame_seconds
        average_text=(
            f"AVG {average:.2f}s" if average is not None and average < 10.0
            else (f"AVG {average:.1f}s" if average is not None else ""))
        canvas.create_text(width-7,6,text=average_text,
                           fill=GRAPH,font=("Segoe UI Semibold",7),anchor="ne")

    def _show_run_details(self,snapshot):
        if snapshot.state is BakeState.ERROR:
            self.run_stats_section.pack_forget()
            if not self.diagnostics_section.winfo_manager():
                self.diagnostics_section.pack(fill="both",expand=True)
        elif not self.run_stats_section.winfo_manager():
            self.diagnostics_section.pack_forget()
            self.run_stats_section.pack(fill="both",expand=True)

    def _fit_window_to_content(self):
        """Keep content from displacing the fixed bottom controls."""
        self.root.update_idletasks()
        width=max(390,self.root.winfo_width())
        requested=max(COMPACT_HEIGHT,self.root.winfo_reqheight())
        height=max(DETAILS_HEIGHT,requested) if self._details_visible else requested
        self.root.geometry(
            f"{width}x{height}+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def _toggle_details(self):
        self._details_visible=not self._details_visible
        if self._details_visible:self.details_panel.grid()
        else:self.details_panel.grid_remove()
        self.details_button.configure(
            text="Hide" if self._details_visible else "Details")
        self._fit_window_to_content()
    def _cancel(self):
        if self._mode is CompanionMode.VEYRA:self._veyra_cancel.set()
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

    def _request_error_guidance(self,error_code):
        code=str(error_code or "").strip().upper()
        self._guidance_code=code
        if not code:return
        self._error_guidance.request(code)

    def _apply_error_guidance(self):
        code=self._guidance_code
        action=self._error_guidance.get(code)
        if not action:return
        updated=replace_recommendation(self._error_details,action)
        if updated==self._error_details:return
        self._error_details=updated
        snapshot=replace(self._last_snapshot,error_details=updated)
        self.secondary.set(details_status(snapshot))
        if self._details_visible:self._fit_window_to_content()

    def _set_activity(self,value,immediate=False):
        value=" ".join(str(value).replace("\\","/").split())
        if ":/" in value or "0x" in value:value="Running solver"
        if not self._solver_status_values(value):value=value[:52]
        if not value or value==self.progress_text.get():value="Running solver"
        self._activity_pending=value
        if immediate:
            self.activity_text.set(value)
            self._draw_status_content()
            return
        if self._activity_after is None:
            def apply():
                self._activity_after=None
                if not self._closed and self._activity_pending:
                    self.activity_text.set(self._activity_pending)
                    self._draw_status_content()
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
                self.status.configure(bg="#85151e" if self._blink_phase else PANEL,
                                      highlightbackground=border)
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
            self.status.configure(bg=PANEL,highlightbackground="#777777")
            self.progress.configure(highlightbackground="#777777")

    def show(self,snapshot: BakeSnapshot):
        self._last_snapshot=snapshot
        self._mode=snapshot.companion_mode
        self.root.title("Cloth NeXt Veyra" if self._mode is CompanionMode.VEYRA
                        else "Cloth NeXt Bake")
        self.root.iconphoto(
            True,self._veyra_icon if self._mode is CompanionMode.VEYRA
            else self._app_icon)
        self.particles.set_mode(self._mode)
        if self._mode is CompanionMode.VEYRA:
            total=snapshot.veyra_step_total
            current=snapshot.veyra_step_current
            self._frame_progress_state=FrameProgress(
                min(1.0,max(0.0,current/total)) if total else 0.0,
                indeterminate=not bool(total))
        else:
            self._frame_progress_state=self._frame_progress.observe(snapshot)
        self.root.update_idletasks()
        width=max(1,self.progress.winfo_width())
        fraction=(min(1.0,max(0.0,snapshot.veyra_step_index/5.0))
                  if self._mode is CompanionMode.VEYRA
                  else snapshot.progress_fraction)
        self._progress_fraction=fraction
        self.progress.coords(self.progress_fill,0,0,width*fraction,22)
        modal = (snapshot.job_kind in {BakeJobKind.BAKE, BakeJobKind.VEYRA}
                 and snapshot.active
                 and self._job_modal)
        if modal != self._job_modal:
            self._job_modal=modal
            _set_bake_window_topmost(self.root,modal)
            if modal:
                self.root.lift()
                self.root.after_idle(self.root.focus_force)
        if self._mode is CompanionMode.VEYRA and snapshot.veyra_step:
            self.progress_text.set(
                f"{VEYRA_STEP_LABELS[snapshot.veyra_step]} · "
                f"Step {snapshot.veyra_step_index} / 5")
        else:self.progress_text.set(progress_display_text(snapshot))
        self.progress.itemconfigure(self.progress_label,text=self.progress_text.get())
        self.progress.coords(self.progress_label,width/2,11)
        self.primary.set(snapshot.error_summary or snapshot.status_title or "Ready")
        self._error_details=snapshot.error_details or ""
        self.secondary.set(details_status(snapshot))
        self.details_meta_text.set(details_meta(snapshot))
        self._update_error_docs_link(snapshot.error_code)
        if snapshot.state is BakeState.ERROR:
            self._request_error_guidance(snapshot.error_code)
        else:
            self._guidance_code=""
        if self._mode is CompanionMode.VEYRA:
            label=(f"{snapshot.veyra_step_current} / {snapshot.veyra_step_total}"
                   if snapshot.veyra_step_total else
                   (snapshot.activity_label or "Working…"))
        else:label=snapshot.activity_label or ACTIVITY_LABELS.get(snapshot.activity_code,"Running solver")
        if snapshot.activity_code is BakeActivity.WRITING_FRAME and snapshot.current_frame is not None:label=f"Writing frame {snapshot.current_frame}"
        if snapshot.state is BakeState.ERROR:
            label=error_activity_label(snapshot)
        self._set_activity(label,snapshot.state in {BakeState.ERROR,BakeState.CANCELLING,BakeState.CANCELLED,BakeState.FINISHED})
        for label,value in run_stats(snapshot):
            self.run_stat_vars[label].set(value)
        self._show_run_details(snapshot)
        self._set_error_blink(snapshot.state is BakeState.ERROR)
        self.particles.set_state(snapshot.state,snapshot.activity_code)
        remaining=format_duration(snapshot.estimated_remaining_seconds,approximate=True)
        self.remaining_text.set("ETA calculating…" if remaining=="Unknown"
                                else f"ETA  {remaining}")
        self.time_text.set(format_duration(snapshot.elapsed_seconds))
        self.cancel.state(["!disabled"] if snapshot.can_cancel else ["disabled"])
        if self._details_visible:
            self._fit_window_to_content()

    def disconnected(self):
        if self._connection_failed:
            return
        if self._last_snapshot.active:
            self.connection_error("CNX-E142",
                                  "The connection to Blender was lost.")
            return
        self._job_modal=False
        _set_bake_window_topmost(self.root,False)
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
        if self._status_fill_after is not None:
            try:self.root.after_cancel(self._status_fill_after)
            except tk.TclError:pass
            self._status_fill_after=None
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
                    elif message["type"]=="veyra_cancel":
                        if str(message["payload"].get("job_id","")) == self._veyra_job_id:
                            self._veyra_cancel.set()
                    elif message["type"]=="shutdown":self.close(); return
                if latest_status is not None:self.show(latest_status)
                self._apply_error_guidance()
                if getattr(self.transport,"closed",False):self.disconnected()
            except (ValueError,PermissionError):
                self.connection_error("CNX-E116",
                                      "The Companion status protocol failed.")
            except OSError:
                self.disconnected()
            if self.root.winfo_exists(): self.root.after(50,poll)
        self.root.after(10,poll); self.root.mainloop()

def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--port",type=int); parser.add_argument("--token"); parser.add_argument("--session-root")
    parser.add_argument("--mode",choices=("bake","veyra"),default="bake")
    args=parser.parse_args(argv); transport=LocalSocketClient(args.port,args.token) if args.port and args.token else DemoTransport()
    try: BakeWindow(transport,session_root=args.session_root,
                    initial_mode=CompanionMode(args.mode.upper())).run()
    except Exception:
        LOG.error("uncaught companion exception\n%s",traceback.format_exc())
        raise
if __name__=="__main__": main()
