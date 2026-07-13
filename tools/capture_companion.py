# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture the real Tk companion for the mandatory human visual gate."""
from pathlib import Path
import sys
import time
from PIL import ImageGrab
import ctypes

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from cloth_next.bake.status import BakeActivity, BakeSnapshot, BakeState
from companion.app import BakeWindow

window=BakeWindow()
window.show(BakeSnapshot(state=BakeState.SIMULATING,preview=True,
    progress_current=7,progress_total=240,current_frame=7,frame_start=1,frame_end=240,
    status_title="Simulating cloth",status_message="Simulating frame 7 of 240",
    activity_code=BakeActivity.SOLVING_CONSTRAINTS,
    elapsed_seconds=2,estimated_remaining_seconds=66,can_cancel=True))
window.root.update_idletasks(); window.root.update(); window.root.lift(); window.root.attributes("-topmost",True)
for _ in range(6):
    window.root.update_idletasks(); window.root.update(); time.sleep(.2)
x,y=window.root.winfo_rootx(),window.root.winfo_rooty(); w,h=window.root.winfo_width(),window.root.winfo_height()
if sys.platform=="win32":
    hwnd=ctypes.windll.user32.GetParent(window.root.winfo_id()) or window.root.winfo_id()
    rect=(ctypes.c_long*4)(); ctypes.windll.user32.GetWindowRect(hwnd,rect)
    x,y,right,bottom=rect; w,h=right-x,bottom-y
output=ROOT/"dist/companion-ui-preview.png"; output.parent.mkdir(exist_ok=True)
ImageGrab.grab((x,y,x+w,y+h),all_screens=True).save(output)
window.close(); print(output)
