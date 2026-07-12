"""Small standard-library source preview for the optional bake companion."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk
import argparse
from cloth_next.bake.status import BakeSnapshot, format_duration
from cloth_next.bake.transport import DemoTransport, LocalSocketClient


class BakeWindow:
    def __init__(self, transport=None, root=None):
        self.transport = transport or DemoTransport()
        self.root = root or tk.Tk()
        self.root.title("Cloth NeXt Bake — UI Preview")
        self.title = tk.StringVar(value="UI Preview — Idle")
        self.detail = tk.StringVar(value="No PPF simulation is running.")
        self.time = tk.StringVar(value="Elapsed 00:00 · Remaining Unknown")
        frame = ttk.Frame(self.root, padding=18); frame.grid(sticky="nsew")
        ttk.Label(frame, textvariable=self.title, font=("Segoe UI", 14, "bold")).grid(sticky="w")
        ttk.Label(frame, textvariable=self.detail).grid(sticky="w", pady=(8, 4))
        self.progress = ttk.Progressbar(frame, length=420, maximum=100); self.progress.grid(sticky="ew")
        ttk.Label(frame, textvariable=self.time).grid(sticky="w", pady=(4, 8))
        ttk.Button(frame, text="Request Cancel", command=self.transport.request_cancel).grid(sticky="e")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def show(self, snapshot: BakeSnapshot):
        marker = "UI Preview — " if snapshot.preview else ""
        self.title.set(marker + snapshot.status_title)
        self.detail.set(snapshot.error_summary or snapshot.status_message)
        self.progress["value"] = snapshot.progress_fraction * 100
        self.time.set(f"Elapsed {format_duration(snapshot.elapsed_seconds)} · Remaining {format_duration(snapshot.estimated_remaining_seconds, approximate=True)}")

    def close(self):
        self.transport.close(); self.root.destroy()

    def run(self):
        def poll():
            try:
                message=self.transport.receive(0.01)
                if message and message["type"]=="bake_status": self.show(message["snapshot"])
                elif message and message["type"]=="session_hello": self.transport.send("ready")
                elif message and message["type"]=="shutdown": self.close(); return
            except (OSError, ValueError, PermissionError):
                self.detail.set("Disconnected — Blender preview is unaffected.")
            if self.root.winfo_exists(): self.root.after(50,poll)
        self.root.after(10,poll); self.root.mainloop()


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--port",type=int); parser.add_argument("--token")
    args=parser.parse_args(argv)
    transport=LocalSocketClient(args.port,args.token) if args.port and args.token else DemoTransport()
    BakeWindow(transport).run()

if __name__ == "__main__": main()
