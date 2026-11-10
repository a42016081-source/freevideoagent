"""Example skill: render a quote card.

Reads SKILL_ARGS (JSON), writes quote.jpg into SKILL_OUTPUT_DIR, prints one JSON result line.
"""
import json
import os
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PALETTES = {
    "motivational": ((20, 20, 60), (200, 90, 30)),
    "sad": ((10, 10, 30), (50, 60, 110)),
    "calm": ((30, 80, 90), (130, 190, 170)),
    "energetic": ((255, 60, 0), (255, 200, 0)),
    "dramatic": ((5, 5, 8), (140, 10, 10)),
}
FONTS = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def font(size):
    for p in FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def gradient(w, h, mood):
    c1, c2 = PALETTES.get(mood, PALETTES["motivational"])
    img = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        px.line([(0, y), (w, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    return img


def main():
    args = json.loads(os.environ.get("SKILL_ARGS") or "{}")
    quote = str(args.get("quote") or "You are capable of amazing things.")[:200]
    author = str(args.get("author") or "")[:60]
    mood = str(args.get("mood") or "motivational")
    out_dir = Path(os.environ.get("SKILL_OUTPUT_DIR") or ".")
    out_dir.mkdir(parents=True, exist_ok=True)

    w, h = 1080, 1920
    img = gradient(w, h, mood)
    d = ImageDraw.Draw(img)
    f = font(88)
    wrapped = textwrap.fill(quote, width=16)
    box = d.multiline_textbbox((0, 0), wrapped, font=f, align="center", spacing=14)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.multiline_text(((w - tw) / 2, (h - th) / 2 - 80), wrapped, fill="white", font=f, align="center",
                     spacing=14, stroke_width=4, stroke_fill="black")
    if author:
        d.text((w / 2, (h + th) / 2 + 10), f"- {author}", fill="white", font=font(46), anchor="mm",
               stroke_width=2, stroke_fill="black")
    out = out_dir / "quote.jpg"
    img.save(out, "JPEG", quality=90)
    print(json.dumps({"ok": True, "kind": "image", "path": str(out)}))


if __name__ == "__main__":
    main()
