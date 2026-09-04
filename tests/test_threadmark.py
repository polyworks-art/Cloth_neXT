import pytest
from PIL import Image
import socket

from cloth_next.provenance.detection import candidate_regions, detect_threadmark
from cloth_next.provenance.payload import ThreadMarkPayloadV1
from cloth_next.provenance.protocol import DecodedSignal, DetectionStatus


def test_payload_v1_roundtrip_and_exact_identity():
    payload = ThreadMarkPayloadV1()
    bits = payload.to_bits()
    assert len(bits) == 40 and ThreadMarkPayloadV1.from_bits(bits) == payload


@pytest.mark.parametrize("index", [0, 16, 27, 39])
def test_payload_rejects_corruption(index):
    bits = list(ThreadMarkPayloadV1().to_bits())
    bits[index] = "1" if bits[index] == "0" else "0"
    with pytest.raises(ValueError):
        ThreadMarkPayloadV1.from_bits("".join(bits))


def test_payload_rejects_wrong_product_even_with_valid_integrity():
    with pytest.raises(ValueError, match="different product"):
        ThreadMarkPayloadV1.from_bits(ThreadMarkPayloadV1(product_id=1).to_bits())


class Decoder:
    def __init__(self, matches):
        self.matches = matches
        self.calls = 0

    def decode(self, _image):
        self.calls += 1
        return DecodedSignal(
            ThreadMarkPayloadV1().to_bits(), self.calls <= self.matches, 0.978
        )


def test_bounded_regions_and_aggregation():
    image = Image.new("RGB", (800, 600))
    assert len(tuple(candidate_regions(image))) == 8
    strong = detect_threadmark(image, Decoder(2))
    assert strong.status is DetectionStatus.DETECTED and strong.regions_matched == 2
    weak = detect_threadmark(image, Decoder(1))
    assert weak.status is DetectionStatus.LIKELY and weak.payload_valid
    absent = detect_threadmark(image, Decoder(0))
    assert absent.status is DetectionStatus.NOT_DETECTED


def test_tiny_image_is_inconclusive_without_decoder_call():
    decoder = Decoder(8)
    result = detect_threadmark(Image.new("RGB", (95, 300)), decoder)
    assert result.status is DetectionStatus.INCONCLUSIVE and decoder.calls == 0


def test_alpha_roundtrip_is_exact(tmp_path):
    from verifier.image_io import encode_file_atomic_result

    source = Image.new("RGBA", (128, 128), (20, 30, 40, 0))
    source.putalpha(Image.linear_gradient("L").resize(source.size))
    path = tmp_path / "alpha.png"
    source.save(path)

    class Encoder:
        def encode(self, image, _bits):
            result = Image.new("RGB", image.size, "red")
            result.putalpha(image.getchannel("A"))
            return result

    outcome = encode_file_atomic_result(
        path, Encoder(), ThreadMarkPayloadV1().to_bits()
    )
    assert outcome.ok, outcome.reason
    with Image.open(path) as result:
        assert result.getchannel("A").tobytes() == source.getchannel("A").tobytes()


def test_unsupported_file_fails_open(tmp_path):
    from verifier.image_io import encode_file_atomic

    path = tmp_path / "render.exr"
    path.write_bytes(b"original")
    assert not encode_file_atomic(path, object(), "0" * 40)
    assert path.read_bytes() == b"original"


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"])
def test_atomic_supported_formats_reopen_successfully(tmp_path, suffix):
    from verifier.image_io import encode_file_atomic_result

    mode = "RGBA" if suffix not in {".jpg", ".jpeg"} else "RGB"
    source = Image.new(mode, (160, 128), (20, 30, 40, 127) if mode == "RGBA" else (20, 30, 40))
    path = tmp_path / f"render{suffix}"
    source.save(path)
    expected_size = source.size
    expected_alpha = source.getchannel("A").tobytes() if mode == "RGBA" else None
    source.close()

    class Encoder:
        def encode(self, image, _bits):
            result = image.convert("RGB")
            result.paste((21, 31, 41), (0, 0, 8, 8))
            if "A" in image.getbands():
                alpha = image.getchannel("A")
                try:
                    result.putalpha(alpha)
                finally:
                    alpha.close()
            return result

    outcome = encode_file_atomic_result(
        path, Encoder(), ThreadMarkPayloadV1().to_bits()
    )
    assert outcome.ok, outcome.reason
    with Image.open(path) as result:
        result.load()
        assert result.size == expected_size
        if mode == "RGBA":
            assert result.getchannel("A").tobytes() == expected_alpha


def test_atomic_encode_exception_preserves_original(tmp_path):
    from verifier.image_io import encode_file_atomic

    path = tmp_path / "render.png"
    Image.new("RGB", (128, 128), "navy").save(path)
    original = path.read_bytes()

    class BrokenEncoder:
        def encode(self, _image, _bits):
            raise RuntimeError("expected test failure")

    assert not encode_file_atomic(path, BrokenEncoder(), "0" * 40)
    assert path.read_bytes() == original
    assert not tuple(tmp_path.glob(".render.*.png"))


def test_worker_protocol_roundtrip_and_bounded_send():
    from cloth_next.provenance.worker_protocol import (
        MAX_MESSAGE_BYTES,
        receive_message,
        send_message,
    )

    sender, receiver = socket.socketpair()
    try:
        send_message(sender, {"type": "ready", "token": "a" * 64})
        assert receive_message(receiver) == {"type": "ready", "token": "a" * 64}
        with pytest.raises(ValueError, match="bounded limit"):
            send_message(sender, {"value": "x" * MAX_MESSAGE_BYTES})
    finally:
        sender.close()
        receiver.close()


def test_worker_exact_payload_verification():
    from verifier.worker import _payload_is_valid

    class Backend:
        def __init__(self, signal):
            self.signal = signal

        def decode(self, _image):
            return self.signal

    exact = DecodedSignal(ThreadMarkPayloadV1().to_bits(), True, 0.99)
    invalid = DecodedSignal("0" * 40, True, 0.99)
    rejected = DecodedSignal(ThreadMarkPayloadV1().to_bits(), False, 0.99)
    assert _payload_is_valid(object(), Backend(exact))
    assert not _payload_is_valid(object(), Backend(invalid))
    assert not _payload_is_valid(object(), Backend(rejected))


def test_render_eligibility_reuses_authenticated_cache(blender_env, monkeypatch):
    import importlib
    from types import SimpleNamespace

    module = importlib.import_module("cloth_next.blender.threadmark_eligibility")
    obj = blender_env.bpy.types.Object("Marked cloth", "MESH")
    obj.cloth_next = SimpleNamespace(
        enabled=True,
        role="CLOTH",
        baked_fingerprint_version=2,
        baked_settings_fingerprint="settings",
        baked_geometry_fingerprint="geometry",
    )
    modifier = obj.modifiers.new("Cloth NeXt Test Cache", "MESH_CACHE")
    modifier.filepath = "C:/cache/cn_test_cloth_valid.pc2"
    obj.cloth_next_playback_owner = "cloth_next_playback_v1"
    obj.cloth_next_cache_path = modifier.filepath
    monkeypatch.setattr(
        module.cache_metadata,
        "inspect_cache",
        lambda *a, **k: SimpleNamespace(usable=True),
    )
    assert module.should_threadmark_render(SimpleNamespace(objects=[obj]))
    obj.cloth_next.enabled = False
    assert not module.should_threadmark_render(SimpleNamespace(objects=[obj]))
