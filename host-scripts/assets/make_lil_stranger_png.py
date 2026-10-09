#!/usr/bin/env python3
"""Make the email-safe Li'l Stranger PNG from https://<host>/lil-stranger/hello.webp.

Most mail clients cannot show WebP, so the nudge email uses a PNG.  The WebP is already RGBA with a
transparent background (1230x1278); this keeps the alpha channel, snaps the lossy-alpha noise
(alpha <= 6 -> 0, >= 250 -> 255) so the edges are clean on any background, and downsizes to 600 px
wide (2x of the 300 px the email displays it at, so it stays sharp on high-DPI screens) using
premultiplied alpha so no dark fringe appears at the edges.

    python3 make_lil_stranger_png.py hello.webp lil-stranger/hello.png      (needs Pillow)

Install on a box (served at https://<LMS_HOST>/media/lil-stranger/hello.png, no restart needed):
    mkdir -p ~/.local/share/tutor/data/openedx-media/lil-stranger
    cp hello.png ~/.local/share/tutor/data/openedx-media/lil-stranger/
"""
import sys

from PIL import Image

WIDTH = 600


def convert(src, dst, width=WIDTH):
    image = Image.open(src).convert("RGBA")
    red, green, blue, alpha = image.split()
    alpha = alpha.point(lambda v: 0 if v <= 6 else (255 if v >= 250 else v))
    image = Image.merge("RGBA", (red, green, blue, alpha))
    height = round(image.height * width / image.width)
    image = image.convert("RGBa").resize((width, height), Image.LANCZOS).convert("RGBA")
    image.save(dst, optimize=True)
    return image.size


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print("wrote %s, %dx%d" % ((sys.argv[2],) + convert(sys.argv[1], sys.argv[2])))
