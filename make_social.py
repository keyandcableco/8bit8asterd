#!/usr/bin/env python3
"""Builds the social preview card at emulator/social.png.

This is what Reddit, Discord, Slack and iMessage show when someone shares the
link. Without one they render a bare url and nobody clicks it.

A real screenshot of the panel beats anything drawn here, so if
`social-source.png` exists it is used instead: it gets fitted onto a
1200x630 canvas in the panel's own colours rather than being cropped, so
nothing important gets cut off. Otherwise a card is drawn from scratch.

    python3 make_social.py
"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "emulator", "social.png")
SOURCE = os.path.join(HERE, "social-source.png")

W, H = 1200, 630

# The NES theme's palette, so the card matches the thing it links to.
SLOT      = (11, 11, 12)
BODY      = (60, 61, 64)
BODY_DARK = (46, 47, 49)
BODY_LIT  = (90, 92, 96)
BEZEL     = (23, 24, 26)
ACCENT    = (200, 50, 43)
TEXT      = (222, 219, 210)
TEXT_DIM  = (140, 141, 144)


def load_font(size, bold=False):
    """Any sane sans will do; the card should never fail for want of a font."""
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def scanlines(img, step=4, strength=18):
    """The faint horizontal banding the panel itself uses."""
    d = ImageDraw.Draw(img, "RGBA")
    for y in range(0, H, step):
        d.line([(0, y), (W, y)], fill=(255, 255, 255, strength))
    return img


def from_screenshot():
    src = Image.open(SOURCE).convert("RGB")
    canvas = Image.new("RGB", (W, H), SLOT)
    # Fit rather than crop: a cropped screenshot usually loses the header,
    # which is the part that identifies what the link even is.
    scale = min(W / src.width, (H - 96) / src.height)
    new = src.resize((int(src.width * scale), int(src.height * scale)), Image.LANCZOS)
    canvas.paste(new, ((W - new.width) // 2, 96 + (H - 96 - new.height) // 2))

    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, W, 88], fill=BODY_DARK)
    d.rectangle([0, 88, W, 96], fill=ACCENT)
    d.text((44, 24), "THE 8BIT 8ASTERD", font=load_font(38, True), fill=TEXT)
    return canvas


def drawn_card():
    img = Image.new("RGB", (W, H), SLOT)
    d = ImageDraw.Draw(img)

    # Console body panel with a bevel, same as the modules in the UI.
    pad = 46
    d.rectangle([pad, pad, W - pad, H - pad], fill=BODY_DARK, outline=BEZEL, width=4)
    d.line([pad + 4, pad + 4, W - pad - 4, pad + 4], fill=BODY_LIT, width=3)

    # The stripe.
    sy = pad + 20
    for i, (x0, x1) in enumerate([(pad + 20, 380), (400, 470), (490, 850), (870, 940), (960, W - pad - 20)]):
        d.rectangle([x0, sy, x1, sy + 12], fill=ACCENT if i % 2 == 0 else BODY)

    d.text((pad + 24, sy + 48), "THE KEY & CABLE COMPANY",
           font=load_font(22, True), fill=TEXT_DIM)
    d.text((pad + 24, sy + 92), "THE 8BIT", font=load_font(96, True), fill=TEXT)
    d.text((pad + 24, sy + 194), "8ASTERD", font=load_font(96, True), fill=TEXT)
    d.rectangle([pad + 24, sy + 312, pad + 24 + 120, sy + 318], fill=ACCENT)

    body = load_font(30)
    lines = [
        "9 voices of chiptune on three AY-3-8910 chips",
        "Circuit-bent effects, drums, historical temperaments",
        "Playable in your browser \u2014 the real firmware, in WebAssembly",
    ]
    y = sy + 348
    for ln in lines:
        d.text((pad + 24, y), ln, font=body, fill=TEXT_DIM)
        y += 44

    # Three chips upper right, clear of the text block below the title.
    cx, cy = W - pad - 296, sy + 132
    for i in range(3):
        x = cx + i * 96
        d.rectangle([x, cy, x + 72, cy + 60], fill=SLOT, outline=BODY_LIT, width=3)
        for pin in range(6):
            px = x + 8 + pin * 11
            d.rectangle([px, cy - 7, px + 6, cy], fill=BODY_LIT)
            d.rectangle([px, cy + 60, px + 6, cy + 67], fill=BODY_LIT)
    return img


def main():
    if os.path.exists(SOURCE):
        img = from_screenshot()
        how = f"from {os.path.basename(SOURCE)}"
    else:
        img = drawn_card()
        how = "drawn (drop a screenshot at social-source.png to use that instead)"
    img = scanlines(img)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT}  {W}x{H}  {how}  ({os.path.getsize(OUT)//1024}KB)")


if __name__ == "__main__":
    main()
