"""One-off generator for public/logo_light.png, logo_dark.png, favicon.ico.

Draws a simple magnifying-glass-plus-node mark (search -> finding) at 4x
resolution and downsamples for antialiasing. Re-run after changing the
colors below if the theme.json palette changes.
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC = os.path.join(HERE, "public")
os.makedirs(PUBLIC, exist_ok=True)

SCALE = 4
SIZE = 256 * SCALE


def draw_mark(color, bg=None):
    img = Image.new("RGBA", (SIZE, SIZE), bg or (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    ring_bbox = (40 * SCALE, 40 * SCALE, 180 * SCALE, 180 * SCALE)
    stroke = 22 * SCALE
    draw.ellipse(ring_bbox, outline=color, width=stroke)

    # Handle: thick line with round caps (simulated with end-circles).
    p1 = (163 * SCALE, 163 * SCALE)
    p2 = (222 * SCALE, 222 * SCALE)
    handle_w = 22 * SCALE
    draw.line([p1, p2], fill=color, width=handle_w)
    for p in (p1, p2):
        r = handle_w // 2
        draw.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=color)

    # Center dot: the "finding".
    cx, cy = 110 * SCALE, 110 * SCALE
    r = 15 * SCALE
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)

    return img.resize((256, 256), Image.LANCZOS)


def main():
    light = draw_mark((80, 72, 229, 255))       # #5048E5 on transparent
    dark = draw_mark((183, 177, 255, 255))      # #B7B1FF on transparent
    light.save(os.path.join(PUBLIC, "logo_light.png"))
    dark.save(os.path.join(PUBLIC, "logo_dark.png"))

    # Favicon: filled rounded-square backdrop so it reads at 16px in a tab.
    fav = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    fdraw = ImageDraw.Draw(fav)
    radius = 48 * SCALE
    fdraw.rounded_rectangle((0, 0, SIZE, SIZE), radius=radius, fill=(80, 72, 229, 255))
    mark = draw_mark((255, 255, 255, 255)).resize((SIZE, SIZE), Image.LANCZOS)
    fav.alpha_composite(mark)
    fav = fav.resize((256, 256), Image.LANCZOS)
    fav.save(
        os.path.join(PUBLIC, "favicon.ico"),
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64)],
    )

    print("Wrote logo_light.png, logo_dark.png, favicon.ico to", PUBLIC)


if __name__ == "__main__":
    main()
