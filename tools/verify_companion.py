# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the ignored development EXE connects and shuts down cleanly."""
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.bake.status import BakeSnapshot, BakeState
from cloth_next.bake.transport import LocalSocketServer

def main():
    exe=ROOT/"companion/dist/Cloth NeXt Bake.exe"
    if not exe.is_file(): raise SystemExit("development EXE missing")
    server=LocalSocketServer()
    process=subprocess.Popen([str(exe),"--port",str(server.port),"--token",server.token])
    try:
        end=time.time()+15; ready=False
        while time.time()<end and not ready:
            ready=server.poll_request()=="ready"; time.sleep(.05)
        if not ready: raise RuntimeError("companion did not authenticate")
        server.publish(BakeSnapshot(state=BakeState.SIMULATING,preview=True,
                                    progress_current=38,progress_total=120))
        server.shutdown_companion(); process.wait(timeout=10)
        if process.returncode: raise RuntimeError(f"companion exited {process.returncode}")
        print("Development EXE: authenticated, received preview, clean shutdown")
    finally:
        if process.poll() is None: process.terminate(); process.wait(timeout=3)
        server.close()
if __name__=="__main__": main()
