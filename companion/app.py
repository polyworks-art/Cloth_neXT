"""Compact dark Cloth NeXt Bake progress companion."""
from __future__ import annotations
import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import math
import random
from pathlib import Path
import sys
import tkinter as tk
import traceback
from tkinter import messagebox, ttk

from cloth_next.bake.status import (ACTIVITY_LABELS, BakeActivity, BakeJobKind,
                                    BakeSnapshot, BakeState, format_duration)
from cloth_next.bake.transport import DemoTransport, LocalSocketClient

BG="#303030"; PANEL="#252525"; BORDER="#555555"; TEXT="#f0f0f0"
MUTED="#b8b8b8"; AMBER="#d99a32"; BUTTON="#444444"

def _logger():
    root=Path(os.environ.get("LOCALAPPDATA",Path.home()))/"Cloth NeXt"/"logs"
    root.mkdir(parents=True,exist_ok=True)
    logger=logging.getLogger("cloth_next_companion")
    if not logger.handlers:
        handler=RotatingFileHandler(root/"companion.log",maxBytes=256*1024,
                                    backupCount=1,encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler); logger.setLevel(logging.INFO)
    return logger

LOG=_logger()

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

class MistAnimation:
    SIZE=76; FRAME_MS=45
    def __init__(self,parent,reduced_motion=False):
        self.canvas=tk.Canvas(parent,width=76,height=76,bg=PANEL,highlightthickness=0,borderwidth=0)
        self.reduced_motion=reduced_motion; self._after=None; self._running=False; self._closed=False
        self._rng=random.Random(81273); self._motion=.12; self._target=.12; self._start=0.; self._layers=[]; self._images=[]
        names=("mist_glow.png","mist_large.png","mist_medium.png","mist_small.png","mist_medium.png","mist_core.png","mist_small.png","mist_core.png")
        try:
            for name in names:
                image=tk.PhotoImage(file=str(_asset(name))); self._images.append(image)
                phase=self._rng.random()*math.tau; radius=self._rng.uniform(2.5,8.)
                self._layers.append((self.canvas.create_image(38,38,image=image),phase,radius,self._rng.uniform(.11,.24)))
            self.available=True
        except Exception:
            LOG.warning("Mist animation unavailable; using static fallback."); self.available=False
            self.canvas.delete("all"); self._images=[]
            try:
                image=tk.PhotoImage(file=str(_asset("mist_fallback.png"))); self._images=[image]; self.canvas.create_image(38,38,image=image)
            except Exception: pass
    def start(self):
        if self._running or self._closed or not self.available:return
        import time
        self._running=True; self._start=time.monotonic(); self._tick()
    def set_state(self,state,activity=BakeActivity.IDLE):
        self._target={BakeState.IDLE:.12,BakeState.PREPARING:.35,BakeState.SIMULATING:.65,
            BakeState.FETCHING:.3,BakeState.IMPORTING:.22,BakeState.FINISHED:.18,
            BakeState.CANCELLED:.03,BakeState.ERROR:.04}.get(state,.42)
        if activity is BakeActivity.SOLVING_CONSTRAINTS:self._target=.72
        elif activity in {BakeActivity.BUILDING_PC2,BakeActivity.APPLYING_PLAYBACK}:self._target=.18
    def _tick(self):
        if self._closed or not self._running:return
        import time
        t=time.monotonic()-self._start; self._motion+=(self._target-self._motion)*.08
        for item,phase,radius,speed in self._layers:
            orbit=0 if self.reduced_motion else radius*self._motion
            self.canvas.coords(item,38+math.cos(t*speed+phase)*orbit,38+math.sin(t*speed*.77+phase)*orbit*.62)
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
        LOG.info("startup pid=%s tk_initialized=true",os.getpid())
        self.root.title("Cloth NeXt Bake"); self.root.configure(bg=BG); self.root.resizable(False,False)
        self.root.geometry("390x118"); self.root.minsize(390,118)
        self._app_icon=tk.PhotoImage(file=str(_asset("cloth_next.png")))
        self.root.iconphoto(True,self._app_icon)
        self.primary=tk.StringVar(value="Ready")
        self.secondary=tk.StringVar(value="No PPF simulation is running.")
        self.progress_text=tk.StringVar(value="Ready")
        self.time_text=tk.StringVar(value="00:00")
        self.remaining_text=tk.StringVar(value="")
        self.activity_text=tk.StringVar(value="Waiting for a Bake")
        self._activity_pending=None; self._activity_after=None; self._closed=False
        self._progress_fraction=0.0
        self._job_modal=False
        self._configure_style(); self._build(); _match_windows_title_bar(self.root)
        self.show(BakeSnapshot()); self.mist.start()
        self.root.protocol("WM_DELETE_WINDOW",self.close)

    def enter_bake_mode(self,payload):
        job_id=str(payload.get("job_id", ""))
        try:
            self.root.deiconify(); self.root.minsize(390,118)
            self.root.update_idletasks()
            if self.root.winfo_width()<100 or self.root.winfo_height()<80:
                self.root.geometry("390x118"); self.root.update_idletasks()
            width=max(390,self.root.winfo_width()); height=max(118,self.root.winfo_height())
            x=max(0,(self.root.winfo_screenwidth()-width)//2)
            y=max(0,(self.root.winfo_screenheight()-height)//2)
            self.root.geometry(f"{width}x{height}+{x}+{y}")
            self.root.update_idletasks()
            if os.environ.get("CLOTH_NEXT_COMPANION_TEST_MODE") == "hidden":
                self.root.withdraw(); self.root.update_idletasks()
            self.root.attributes("-topmost",True); self.root.lift()
            self.root.after_idle(self.root.focus_force); self.root.update_idletasks()
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
        style=ttk.Style(self.root); style.theme_use("clam")
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
        icon_box=tk.Frame(body,bg=PANEL,highlightbackground=BORDER,highlightthickness=1,width=82,height=76)
        icon_box.grid(row=0,column=0,rowspan=2,sticky="ns",padx=(0,5)); icon_box.grid_propagate(False)
        self.mist=MistAnimation(icon_box,os.environ.get("CLOTH_NEXT_REDUCED_MOTION")=="1")
        self.mist.canvas.place(relx=.5,rely=.5,anchor="center")
        right=ttk.Frame(body,style="CN.TFrame"); right.grid(row=0,column=1,sticky="ew"); body.columnconfigure(1,weight=1)
        self.progress=tk.Canvas(right,width=270,height=22,bg=PANEL,highlightbackground="#777777",highlightthickness=1,borderwidth=0)
        self.progress.grid(row=0,column=0,sticky="ew"); right.columnconfigure(0,weight=1)
        self.progress_fill=self.progress.create_rectangle(0,0,0,22,fill=AMBER,outline="")
        self.progress_label=self.progress.create_text(136,11,text="Ready",fill=TEXT,font=("Segoe UI",8))
        self.progress.bind("<Configure>",self._resize_progress)
        status=tk.Label(right,textvariable=self.activity_text,bg=PANEL,fg=TEXT,font=("Segoe UI",8),anchor="center",justify="center",
                        highlightbackground="#777777",highlightthickness=1,height=1)
        status.grid(row=1,column=0,sticky="ew",pady=(5,0),ipady=3)
        bottom=ttk.Frame(outer,style="CN.TFrame",height=30); bottom.grid(row=1,column=0,sticky="ew",pady=(5,0))
        self.pause=ttk.Button(bottom,text="Pause",width=8,style="CN.TButton",state="disabled",command=self._pause)
        self.pause.pack(side="left")
        ttk.Label(bottom,textvariable=self.time_text,style="CN.TLabel",anchor="center",justify="center").place(relx=.5,rely=.5,anchor="center")
        self.cancel=ttk.Button(bottom,text="Cancel",width=8,style="CN.TButton",command=self._cancel)
        self.cancel.pack(side="right")
        ttk.Button(bottom,text="?",width=3,style="CN.TButton",command=self._about).pack(side="right",padx=(5,5))

    def _resize_progress(self,event=None):
        width=max(1,event.width if event is not None else self.progress.winfo_width())
        self.progress.coords(self.progress_fill,0,0,width*self._progress_fraction,22)
        self.progress.coords(self.progress_label,width/2,11)

    def _about(self): messagebox.showinfo("About Cloth NeXt Bake","SideFX, please don’t sue me.",parent=self.root)
    def _pause(self): pass
    def _cancel(self):
        self.transport.request_cancel(); self.primary.set("Cancelling…"); self.cancel.state(["disabled"])

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

    def show(self,snapshot: BakeSnapshot):
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
        self.secondary.set(snapshot.error_details or snapshot.status_message or "No PPF simulation is running.")
        label=snapshot.activity_label or ACTIVITY_LABELS.get(snapshot.activity_code,"Running solver")
        if snapshot.activity_code is BakeActivity.WRITING_FRAME and snapshot.current_frame is not None:label=f"Writing frame {snapshot.current_frame}"
        if snapshot.state is BakeState.ERROR and snapshot.activity_detail:label=f"Failed while {snapshot.activity_detail}"
        self._set_activity(label,snapshot.state in {BakeState.ERROR,BakeState.CANCELLING,BakeState.CANCELLED,BakeState.FINISHED})
        self.mist.set_state(snapshot.state,snapshot.activity_code)
        self.time_text.set(format_duration(snapshot.elapsed_seconds))
        remaining=format_duration(snapshot.estimated_remaining_seconds,approximate=True)
        self.remaining_text.set("" if remaining=="Unknown" else f"remaining {remaining}")
        self.cancel.state(["!disabled"] if snapshot.can_cancel else ["disabled"])
        self.pause.state(["!disabled"] if snapshot.can_pause else ["disabled"])
        self.pause.configure(text="Resume" if snapshot.is_paused else "Pause")

    def disconnected(self):
        self._job_modal=False
        self.root.attributes("-topmost",False)
        self.primary.set("Disconnected from Blender"); self.secondary.set("Blender-side work is unaffected.")
        self.cancel.state(["disabled"]); self.pause.state(["disabled"])

    def close(self):
        if self._closed:return
        self._closed=True; self.mist.close()
        if self._activity_after is not None:
            try:self.root.after_cancel(self._activity_after)
            except tk.TclError:pass
        self.transport.close(); self.root.destroy()
    def run(self):
        def poll():
            try:
                message=self.transport.receive(.01)
                if message and message["type"]=="bake_status": self.show(message["snapshot"])
                elif message and message["type"]=="session_hello": self.transport.send("ready")
                elif message and message["type"]=="enter_bake_mode": self.enter_bake_mode(message["payload"])
                elif message and message["type"]=="shutdown": self.close(); return
                elif getattr(self.transport,"closed",False): self.disconnected()
            except (OSError,ValueError,PermissionError): self.disconnected()
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
