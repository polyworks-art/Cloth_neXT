"""White outline icons (64 px, transparent) for the AETHER quality presets, in the style of cloth_next/assets/icons.

Normal: a shirt. Animation: the shirt with speed lines. Fitting: the shirt with a measuring tape across the chest.
Drawn at 4x and downsampled for antialiasing.
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(sys.argv[1])
S = 256          # drawing size, downsampled to 64
W = 15           # stroke width at drawing size (about 3.75 px at 64)


def poly(d, pts, width=W, closed=True):
    pts = list(pts) + ([pts[0]] if closed else [])
    d.line(pts, fill=(255, 255, 255, 255), width=width, joint="curve")
    r = width / 2
    for x, y in pts:
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 255))


def shirt(d, ox=0, scale=1.0):
    # a T-shirt with a round neck, in a 256 box
    p = [(70, 52), (100, 40), (114, 58), (142, 58), (156, 40), (186, 52), (222, 96), (196, 120), (180, 104),
         (180, 214), (76, 214), (76, 104), (60, 120), (34, 96)]
    p = [(ox + 128 + (x - 128) * scale, 128 + (y - 128) * scale) for x, y in p]
    poly(d, p)
    # neck curve
    import math
    cx, cy = ox + 128, 128 + (58 - 128) * scale
    arc = [(cx + 14 * scale * math.cos(a), cy + 14 * scale * math.sin(a))
           for a in [math.pi * k / 8 for k in range(0, 9)]]
    poly(d, arc, closed=False)


def save(img, name):
    img.resize((64, 64), Image.LANCZOS).save(OUT / f"{name}.png")


def normal():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shirt(ImageDraw.Draw(img))
    save(img, "quality_normal")


def animation():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    shirt(d, ox=22, scale=0.86)
    for y, x0 in ((118, 18), (150, 6), (182, 18)):   # speed lines left of the shirt
        poly(d, [(x0, y), (x0 + 40, y)], closed=False)
    save(img, "quality_animation")


def fitting():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    shirt(d)
    # measuring tape across the chest, reaching past both sides, with ticks
    poly(d, [(44, 150), (212, 150)], width=W - 2, closed=False)
    for x in range(96, 172, 19):
        poly(d, [(x, 150), (x, 132)], width=W - 7, closed=False)
    save(img, "quality_fitting")


OUT.mkdir(parents=True, exist_ok=True)
normal()
animation()
fitting()
print("ok")
