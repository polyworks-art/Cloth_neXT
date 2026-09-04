"""Windowed and CLI ThreadMark verifier; never embeds user images."""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.provenance.detection import detect_threadmark  # noqa: E402
from verifier.trustmark_backend import TrustMarkOnnxBackend  # noqa: E402


def _models() -> Path:
    bundled = Path(getattr(sys, "_MEIPASS", ROOT)) / "threadmark_models"
    return Path(os.environ.get("THREADMARK_MODEL_DIR", bundled))


def verify(path: Path, variant="Q"):
    from PIL import Image

    backend = TrustMarkOnnxBackend(_models(), variant=variant)
    with Image.open(path) as image:
        image.load()
        return detect_threadmark(image, backend)


def _emit(text: str) -> None:
    """Write CLI output from a Windows GUI-subsystem executable."""
    data = (text + "\n").encode("utf-8")
    stream = getattr(sys, "stdout", None)
    if stream is not None:
        try:
            stream.buffer.write(data)
            stream.flush()
            return
        except OSError:
            sys.stdout = None
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        written = ctypes.c_ulong()
        if handle not in (0, -1):
            ctypes.windll.kernel32.WriteFile(
                handle, data, len(data), ctypes.byref(written), None
            )


def run_window() -> int:
    import tkinter as tk
    from tkinter import filedialog, ttk

    root = tk.Tk()
    root.title("Cloth NeXt Verify")
    root.geometry("520x330")
    root.configure(bg="#17191d")
    title = tk.Label(
        root,
        text="Cloth NeXt Verify",
        fg="#f3f3f3",
        bg="#17191d",
        font=("Segoe UI", 18, "bold"),
    )
    title.pack(pady=(28, 6))
    status = tk.StringVar(value="Open an image to check for ThreadMark.")
    detail = tk.StringVar(value="")
    tk.Label(
        root,
        textvariable=status,
        fg="#d99a32",
        bg="#17191d",
        font=("Segoe UI", 14, "bold"),
    ).pack(pady=12)
    tk.Label(
        root,
        textvariable=detail,
        fg="#c8cbd0",
        bg="#17191d",
        justify="left",
        font=("Segoe UI", 10),
    ).pack(pady=4)

    def open_image():
        selected = filedialog.askopenfilename(
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.webp *.tif *.tiff"),
                ("All files", "*.*"),
            ]
        )
        if not selected:
            return
        status.set("Analyzing…")
        root.update_idletasks()
        try:
            result = verify(Path(selected))
            labels = {
                "DETECTED": "ThreadMark detected",
                "LIKELY": "Likely detected",
                "INCONCLUSIVE": "Inconclusive",
                "NOT_DETECTED": "Not detected",
            }
            status.set(labels[result.status.value])
            reason = result.diagnostics.get("reason", "")
            detail.set(
                f"Confidence: {result.confidence * 100:.1f}%\nSchema: {result.schema_version or '—'}\n"
                f"Product: {'Cloth NeXt' if result.payload_valid else '—'}\n"
                f"Regions matched: {result.regions_matched} / {result.regions_tested}"
                + (f"\nReason: {reason}" if reason else "")
            )
        except Exception as exc:
            status.set("Inconclusive")
            detail.set(f"The image could not be analyzed.\nReason: {type(exc).__name__}")

    ttk.Button(root, text="Open Image", command=open_image).pack(pady=22)
    root.mainloop()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="Cloth NeXt Verify")
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--variant", choices=("P", "Q"), default="Q")
    parser.add_argument("--threadmark-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--token", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.threadmark_worker:
        if args.verify or args.host != "127.0.0.1" or not args.port or not args.token:
            return 2
        from verifier.worker import run_worker

        return run_worker(
            host=args.host, port=args.port, token=args.token, model_dir=_models()
        )
    if not args.verify:
        return run_window()
    try:
        result = verify(args.verify, args.variant)
    except Exception as exc:
        payload = {
            "status": "INCONCLUSIVE",
            "confidence": 0.0,
            "payload_valid": False,
            "schema_version": None,
            "regions_tested": 0,
            "regions_matched": 0,
            "diagnostics": {"reason": f"image analysis failed ({type(exc).__name__})"},
        }
        _emit(json.dumps(payload, sort_keys=True))
        return 2
    _emit(
        json.dumps(result.to_dict(), sort_keys=True)
        if args.json
        else result.status.value
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
