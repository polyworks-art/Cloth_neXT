from pathlib import Path
import subprocess
from PIL import Image

from companion.build_assets import build
from tools.build_brand_assets import build as build_brand_assets
from companion.build_assets import (APP_ICON_SIZE, PARTICLE_ASSETS,
                                    PARTICLE_SUBPIXEL_ASSETS,
                                    PARTICLE_SUBPIXEL_PHASES,
                                    PARTICLE_VISUAL_SCALE, STATUS_ASSETS,
                                    STATUS_SIZE)
from companion.app import error_activity_label
from cloth_next.bake.status import BakeSnapshot,BakeState

ROOT = Path(__file__).resolve().parents[1]


def test_all_identity_derivatives_are_deterministic_from_primary_mark():
    source = ROOT / "assets" / "CN_new_Logo.svg"
    assert 'viewBox="0 0 497 476"' in source.read_text(encoding="utf-8")
    outputs = (
        ROOT / "assets" / "Logo_CN.png",
        ROOT / "assets" / "Logo_CN_BW.png",
        ROOT / "assets" / "LOGO_addon.png",
        ROOT / "assets" / "Cloth_neXt_icon.svg",
        ROOT / "assets" / "cloth-next-white.svg",
        ROOT / "assets" / "cloth_next_icons" / "cloth_next.svg",
        ROOT / "cloth_next" / "assets" / "icons" / "cloth_next.png",
        ROOT / "cloth_next" / "resources" / "onboarding" / "assets" /
        "cloth-next-logo.png",
        ROOT / "cloth_next" / "resources" / "onboarding" / "assets" /
        "cloth-next-logo-splash.png",
        ROOT / "cloth_next" / "resources" / "onboarding" / "assets" /
        "cloth-next-logo.svg",
        ROOT / "cloth_next" / "resources" / "onboarding" / "icons" /
        "logo.png",
    )
    before = {path: path.read_bytes() for path in outputs}
    build_brand_assets()
    assert before == {path: path.read_bytes() for path in outputs}


def test_identity_sources_follow_primary_mark_clear_space_and_color():
    color = ROOT / "assets" / "Logo_CN.png"
    monochrome = ROOT / "assets" / "Logo_CN_BW.png"
    for path, expected_rgb in ((color, (17, 17, 17)),
                               (monochrome, (245, 245, 243))):
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            bounds = rgba.getchannel("A").getbbox()
            assert rgba.size == (1024, 1024)
            assert bounds == (128, 131, 896, 893)
            assert all(pixel[:3] == expected_rgb
                       for pixel in rgba.get_flattened_data() if pixel[3])
    assert (ROOT / "assets" / "LOGO_addon.png").read_bytes() == color.read_bytes()


def test_companion_assets_reuse_approved_identity_and_bake_icons():
    build()
    target = ROOT / "companion" / "assets"
    first_identity = {
        name: (target / name).read_bytes()
        for name in ("cloth_next.png", "cloth_next.ico")}
    build()
    assert first_identity == {
        name: (target / name).read_bytes()
        for name in ("cloth_next.png", "cloth_next.ico")}
    source = ROOT / "cloth_next" / "assets" / "icons"
    with Image.open(target / "cloth_next.png") as actual, \
            Image.open(ROOT / "assets" / "Logo_CN_BW.png") as approved:
        rgba = actual.convert("RGBA")
        visible_bounds = rgba.getchannel("A").getbbox()
        assert actual.size == APP_ICON_SIZE == (256, 256)
        assert visible_bounds == (29, 30, 226, 226)
        assert approved.size == (1024, 1024)
        assert all(pixel[:3] == (245, 245, 243)
                   for pixel in rgba.get_flattened_data() if pixel[3])
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

def test_particle_assets_are_deterministic_translucent_icons():
    build(); target=ROOT/"companion"/"assets"
    all_particles={**PARTICLE_ASSETS,**PARTICLE_SUBPIXEL_ASSETS}
    before={name:(target/name).read_bytes() for name in all_particles}; build()
    assert before == {name:(target/name).read_bytes() for name in all_particles}
    assert PARTICLE_SUBPIXEL_PHASES == 4
    assert PARTICLE_VISUAL_SCALE == 1.05
    assert len(PARTICLE_SUBPIXEL_ASSETS) == len(PARTICLE_ASSETS)*16
    for name,size in all_particles.items():
        assert (target/name).stat().st_size < 16*1024
        with Image.open(target/name) as image:
            rgba=image.convert("RGBA")
            assert image.mode=="RGBA" and image.size==size
            visible=[pixel for pixel in rgba.get_flattened_data() if pixel[3]]
            assert visible and max(pixel[3] for pixel in visible) <= 184
    phases=[(target/name).read_bytes() for name in PARTICLE_SUBPIXEL_ASSETS
            if name.startswith("particle_bake_12.subpixel-")]
    assert len(set(phases)) == 16


def test_solver_status_assets_are_deterministic_opaque_white_icons():
    build(); target=ROOT/"companion"/"assets"
    before={name:(target/name).read_bytes() for name in STATUS_ASSETS}; build()
    assert before == {name:(target/name).read_bytes() for name in STATUS_ASSETS}
    for name in STATUS_ASSETS:
        with Image.open(target/name) as image:
            rgba=image.convert("RGBA")
            visible=[pixel for pixel in rgba.get_flattened_data() if pixel[3]]
            assert image.size == STATUS_SIZE
            assert visible
            assert all(pixel[:3] == (255,255,255) for pixel in visible)


def test_blender_runtime_icons_are_white_for_dark_theme():
    source = ROOT / "cloth_next" / "assets" / "icons"
    for path in source.glob("*.png"):
        with Image.open(path) as image:
            visible = [pixel for pixel in image.convert("RGBA").get_flattened_data()
                       if pixel[3]]
            assert visible, path
        if path.name == "gaia_engine.png":
            continue
        expected = ((245, 245, 243) if path.name == "cloth_next.png"
                    else (255, 255, 255))
        assert all(pixel[:3] == expected for pixel in visible), path


def test_gaia_engine_brand_icon_is_small_and_transparent():
    path = ROOT / "cloth_next" / "assets" / "icons" / "gaia_engine.png"
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        assert image.size == (64, 64)
        assert rgba.getpixel((0, 0))[3] == 0
        assert any(pixel[3] and pixel[:3] != (255, 255, 255)
                   for pixel in rgba.get_flattened_data())


def test_companion_error_bar_uses_only_stable_code():
    snapshot=BakeSnapshot(state=BakeState.ERROR,error_code="CNX-E160",
        error_summary="private full summary",error_details="private details")
    label=error_activity_label(snapshot)
    assert label=="ERROR · CNX-E160"
    assert "private" not in label
