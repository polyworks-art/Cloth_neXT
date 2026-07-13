from pathlib import Path
import subprocess
from PIL import Image

from companion.build_assets import build
from companion.build_assets import MIST_ASSETS

ROOT = Path(__file__).resolve().parents[1]


def test_companion_assets_reuse_approved_identity_and_bake_icons():
    build()
    target = ROOT / "companion" / "assets"
    source = ROOT / "cloth_next" / "assets" / "icons"
    assert (target / "cloth_next.png").read_bytes() == (source / "cloth_next.png").read_bytes()
    with Image.open(target/"bake.png") as derived, Image.open(source/"bake.png") as approved:
        assert derived.getchannel("A").tobytes() == approved.convert("RGBA").getchannel("A").tobytes()
        assert derived.getpixel((derived.width//2,derived.height//2))[:3] in {(217,154,50),(0,0,0)}
    with Image.open(target / "cloth_next.ico") as icon:
        assert icon.format == "ICO"
        assert (256, 256) in icon.info["sizes"]


def test_generated_companion_executable_is_not_committed():
    tracked=subprocess.run(["git", "ls-files", "cloth_next"], cwd=ROOT,
                           check=True, capture_output=True, text=True).stdout.splitlines()
    executables=[path for path in tracked if path.lower().endswith(".exe")]
    assert executables == []

def test_mist_assets_are_deterministic_full_frame_rgb():
    build(); target=ROOT/"companion"/"assets"
    before={name:(target/name).read_bytes() for name in MIST_ASSETS}; build()
    assert before == {name:(target/name).read_bytes() for name in MIST_ASSETS}
    for name,size in MIST_ASSETS.items():
        assert (target/name).stat().st_size < 64*1024
        with Image.open(target/name) as image:
            assert image.mode=="RGB" and image.size==size
            assert all(image.getpixel(point)!=(0,0,0) for point in ((0,0),(size[0]-1,0),(0,size[1]-1),(size[0]-1,size[1]-1)))


def test_blender_runtime_icons_are_white_for_dark_theme():
    source = ROOT / "cloth_next" / "assets" / "icons"
    for path in source.glob("*.png"):
        with Image.open(path) as image:
            visible = [pixel for pixel in image.convert("RGBA").getdata()
                       if pixel[3]]
            assert visible, path
            assert all(pixel[:3] == (255, 255, 255) for pixel in visible), path
