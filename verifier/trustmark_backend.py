"""ONNX TrustMark implementation behind the ThreadMark interfaces.

TrustMark source is Copyright 2023-2025 Adobe and MIT licensed. This module is
an independent Python adaptation of the preprocessing documented by Adobe's
official Rust implementation; see ``docs/THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations
from pathlib import Path

from cloth_next.provenance.protocol import (
    DecodedSignal,
    ThreadMarkDecoder,
    ThreadMarkEncoder,
)


class TrustMarkOnnxBackend(ThreadMarkEncoder, ThreadMarkDecoder):
    """Offline P/Q encoder-decoder. Models must already exist on disk."""

    def __init__(self, model_dir, *, variant="Q", strength=0.8, threads=4):
        if variant not in {"P", "Q"} or not 0 < strength <= 2:
            raise ValueError("invalid watermark configuration")
        try:
            import bchlib
            import numpy as np
            import onnxruntime as ort
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(
                "ThreadMark ONNX runtime dependencies are unavailable"
            ) from exc
        self._np, self._Image, self._bch = np, Image, bchlib.BCH(8, 137)
        self.variant, self.strength = variant, float(strength)
        root = Path(model_dir)
        encoder, decoder = (
            root / f"encoder_{variant}.onnx",
            root / f"decoder_{variant}.onnx",
        )
        if not encoder.is_file() or not decoder.is_file():
            raise FileNotFoundError("pre-provisioned TrustMark ONNX models are missing")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(8, int(threads)))
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        providers = ["CPUExecutionProvider"]
        self._encoder = ort.InferenceSession(str(encoder), options, providers=providers)
        self._decoder = ort.InferenceSession(str(decoder), options, providers=providers)

    def _crop(self, image):
        w, h = image.size
        if self.variant == "P" or max(w, h) > 2 * min(w, h):
            edge = min(w, h)
            left = (w - edge) // 2
            top = (h - edge) // 2
            return image.crop((left, top, left + edge, top + edge))
        return image

    def _tensor(self, image, size):
        Image, np = self._Image, self._np
        rgb = (
            self._crop(image)
            .convert("RGB")
            .resize((size, size), Image.Resampling.BILINEAR)
        )
        array = np.asarray(rgb, dtype=np.float32) / 127.5 - 1.0
        return np.transpose(array, (2, 0, 1))[None].astype(np.float32)

    def _packet(self, payload_bits):
        if len(payload_bits) != 40 or set(payload_bits) - {"0", "1"}:
            raise ValueError("BCH_SUPER requires a 40-bit payload")
        data = bytearray(int(payload_bits[i : i + 8], 2) for i in range(0, 40, 8))
        ecc = self._bch.encode(data)
        packet = payload_bits + "".join(f"{v:08b}" for v in ecc)[:56] + "0000"
        return self._np.asarray([[int(bit) for bit in packet]], dtype=self._np.float32)

    def _resize_float(self, array, size):
        Image, np = self._Image, self._np
        channels = [
            np.asarray(
                Image.fromarray(array[..., c], mode="F").resize(
                    size, Image.Resampling.BILINEAR
                ),
                dtype=np.float32,
            )
            for c in range(3)
        ]
        return np.stack(channels, axis=-1)

    def encode(self, image, payload_bits):
        np = self._np
        model_input = self._tensor(image, 256)
        output = self._encoder.run(
            ["image"],
            {"onnx::Concat_0": model_input, "onnx::Gemm_1": self._packet(payload_bits)},
        )[0]
        multiplier = 1.25 if self.variant == "P" else 1.0
        residual = np.clip(
            (output - model_input) * multiplier * self.strength, -0.2, 0.2
        )
        residual = np.transpose(residual[0], (1, 2, 0))
        residual = self._resize_float(residual, image.size)
        original = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
        marked = np.clip(original + residual / 2.0, 0.0, 1.0)
        result = self._Image.fromarray(np.rint(marked * 255).astype(np.uint8), "RGB")
        if "A" in image.getbands():
            alpha = image.getchannel("A")
            try:
                result.putalpha(alpha)
            finally:
                alpha.close()
        return result

    def decode(self, image):
        np = self._np
        size = 224 if self.variant == "P" else 256
        logits = self._decoder.run(["output"], {"image": self._tensor(image, size)})[0][
            0
        ]
        bits = "".join("1" if value >= 0 else "0" for value in logits)
        if bits[96:] != "0000":
            return DecodedSignal(
                confidence=0.0, diagnostics={"reason": "non-BCH_SUPER schema"}
            )
        data = bytearray(int(bits[i : i + 8], 2) for i in range(0, 40, 8))
        ecc = bytearray(int(bits[i : i + 8], 2) for i in range(40, 96, 8))
        flips = self._bch.decode(data, ecc)
        if flips < 0:
            return DecodedSignal(
                confidence=0.0, diagnostics={"reason": "ECC rejected payload"}
            )
        self._bch.correct(data, ecc)
        payload = "".join(f"{value:08b}" for value in data)[:40]
        certainty = float(
            np.mean(1.0 / (1.0 + np.exp(-np.minimum(np.abs(logits), 30.0))))
        )
        return DecodedSignal(
            payload,
            True,
            certainty,
            {"corrected_bits": int(flips), "variant": self.variant},
        )
