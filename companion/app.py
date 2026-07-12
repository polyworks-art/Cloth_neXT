"""Compact dark Cloth NeXt Bake progress companion."""
from __future__ import annotations
import argparse
import ctypes
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from cloth_next.bake.status import BakeSnapshot, BakeState, format_duration
from cloth_next.bake.transport import DemoTransport, LocalSocketClient

BG="#303030"; PANEL="#252525"; BORDER="#555555"; TEXT="#f0f0f0"
MUTED="#b8b8b8"; AMBER="#d99a32"; BUTTON="#444444"

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

class BakeWindow:
    def __init__(self,transport=None,root=None):
        _windows_identity(); self.transport=transport or DemoTransport(); self.root=root or tk.Tk()
        self.root.title("Cloth NeXt Bake"); self.root.configure(bg=BG); self.root.resizable(False,False)
        self.root.geometry("370x108"); self.root.minsize(370,108)
        self._app_icon=tk.PhotoImage(file=str(_asset("cloth_next.png")))
        self._bake_icon=tk.PhotoImage(file=str(_asset("bake.png"))).subsample(2,2)
        self.root.iconphoto(True,self._app_icon)
        self.primary=tk.StringVar(value="Ready")
        self.secondary=tk.StringVar(value="No PPF simulation is running.")
        self.progress_text=tk.StringVar(value="Ready")
        self.time_text=tk.StringVar(value="00:00")
        self.remaining_text=tk.StringVar(value="")
        self._progress_fraction=0.0
        self._configure_style(); self._build(); self.show(BakeSnapshot())
        self.root.protocol("WM_DELETE_WINDOW",self.close)

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
        icon_box=tk.Frame(body,bg=PANEL,highlightbackground=BORDER,highlightthickness=1,width=66,height=65)
        icon_box.grid(row=0,column=0,rowspan=2,sticky="ns",padx=(0,5)); icon_box.grid_propagate(False)
        tk.Label(icon_box,image=self._bake_icon,bg=PANEL).place(relx=.5,rely=.5,anchor="center")
        right=ttk.Frame(body,style="CN.TFrame"); right.grid(row=0,column=1,sticky="ew"); body.columnconfigure(1,weight=1)
        self.progress=tk.Canvas(right,width=270,height=22,bg=PANEL,highlightbackground="#777777",highlightthickness=1,borderwidth=0)
        self.progress.grid(row=0,column=0,sticky="ew"); right.columnconfigure(0,weight=1)
        self.progress_fill=self.progress.create_rectangle(0,0,0,22,fill=AMBER,outline="")
        self.progress_label=self.progress.create_text(136,11,text="Ready",fill=TEXT,font=("Segoe UI",8))
        self.progress.bind("<Configure>",self._resize_progress)
        status=tk.Label(right,textvariable=self.secondary,bg=PANEL,fg=TEXT,font=("Segoe UI",8),anchor="center",justify="center",
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

    def show(self,snapshot: BakeSnapshot):
        self.root.update_idletasks()
        width=max(1,self.progress.winfo_width()); fraction=snapshot.progress_fraction
        self._progress_fraction=fraction
        self.progress.coords(self.progress_fill,0,0,width*fraction,22)
        if snapshot.current_frame is not None and snapshot.frame_end is not None:
            self.progress_text.set(f"render frame {snapshot.current_frame} / {snapshot.frame_end}")
        elif snapshot.progress_total:
            self.progress_text.set(f"{snapshot.progress_fraction:.0%}")
        else: self.progress_text.set(snapshot.status_title or "Ready")
        self.progress.itemconfigure(self.progress_label,text=self.progress_text.get())
        self.progress.coords(self.progress_label,width/2,11)
        self.primary.set(snapshot.error_summary or snapshot.status_title or "Ready")
        self.secondary.set(snapshot.error_details or snapshot.status_message or "No PPF simulation is running.")
        self.time_text.set(format_duration(snapshot.elapsed_seconds))
        remaining=format_duration(snapshot.estimated_remaining_seconds,approximate=True)
        self.remaining_text.set("" if remaining=="Unknown" else f"remaining {remaining}")
        self.cancel.state(["!disabled"] if snapshot.can_cancel else ["disabled"])
        self.pause.state(["!disabled"] if snapshot.can_pause else ["disabled"])
        self.pause.configure(text="Resume" if snapshot.is_paused else "Pause")

    def disconnected(self):
        self.primary.set("Disconnected from Blender"); self.secondary.set("Blender-side work is unaffected.")
        self.cancel.state(["disabled"]); self.pause.state(["disabled"])

    def close(self): self.transport.close(); self.root.destroy()
    def run(self):
        def poll():
            try:
                message=self.transport.receive(.01)
                if message and message["type"]=="bake_status": self.show(message["snapshot"])
                elif message and message["type"]=="session_hello": self.transport.send("ready")
                elif message and message["type"]=="shutdown": self.close(); return
                elif getattr(self.transport,"closed",False): self.disconnected()
            except (OSError,ValueError,PermissionError): self.disconnected()
            if self.root.winfo_exists(): self.root.after(50,poll)
        self.root.after(10,poll); self.root.mainloop()

def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--port",type=int); parser.add_argument("--token")
    args=parser.parse_args(argv); transport=LocalSocketClient(args.port,args.token) if args.port and args.token else DemoTransport()
    BakeWindow(transport).run()
if __name__=="__main__": main()
