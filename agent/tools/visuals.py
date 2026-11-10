"""Scene visuals. Per scene, in the order given by config providers.image_order:

    pexels  (video if PEXELS_API_KEY)  ->  nvidia / pollinations (AI image)  ->  pixabay (photo)
    -> generated gradient (always works, offline)

Returns ("video", path) or ("image", path). Images are saved 1.5x larger than the frame so the
Ken Burns zoom stays sharp.
"""
from __future__ import annotations

import hashlib
import logging
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from agent.config import settings
from agent.providers.image import generate_image

log = logging.getLogger(__name__)

PALETTES = {
    "motivational": ((20, 20, 60), (200, 90, 30)),
    "sad": ((10, 10, 30), (50, 60, 110)),
    "energetic": ((255, 60, 0), (255, 200, 0)),
    "calm": ((30, 80, 90), (130, 190, 170)),
    "dramatic": ((5, 5, 8), (140, 10, 10)),
}


def cover_resize(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale + centre-crop so the image fully covers w x h (no distortion)."""
    scale = max(w / img.width, h / img.height)
    nw, nh = max(int(img.width * scale + 0.5), w), max(int(img.height * scale + 0.5), h)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def gradient_image(w: int, h: int, mood: str, seed: int = 0) -> Image.Image:
    c1, c2 = PALETTES.get(mood, PALETTES["motivational"])
    t = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    arr = np.array(c1, dtype=np.float32) * (1 - t) + np.array(c2, dtype=np.float32) * t
    arr = np.repeat(arr, w, axis=1)
    rng = np.random.default_rng(seed)
    arr += rng.integers(0, 10, (h, w, 1)).astype(np.float32)          # light film grain
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _pexels(query: str, w: int, h: int, workdir: Path, key: str) -> tuple[str, Path] | None:
    if not settings.PEXELS_API_KEY:
        return None
    orient = "portrait" if h > w else "landscape" if w > h else "square"
    try:
        r = requests.get("https://api.pexels.com/videos/search",
                         headers={"Authorization": settings.PEXELS_API_KEY},
                         params={"query": query, "per_page": 3, "orientation": orient}, timeout=20)
        r.raise_for_status()
        for v in r.json().get("videos", []):
            files = sorted(v.get("video_files", []), key=lambda f: f.get("width", 0))
            pick = next((f for f in files if f.get("width", 0) >= min(w, h) * 0.6), None) \
                or (files[-1] if files else None)
            if not pick:
                continue
            out = workdir / f"stock_{key}.mp4"
            with requests.get(pick["link"], stream=True, timeout=60) as resp:
                resp.raise_for_status()
                with open(out, "wb") as fh:
                    for chunk in resp.iter_content(1 << 16):
                        fh.write(chunk)
            return "video", out
    except Exception as e:  # noqa: BLE001
        log.warning("Pexels failed: %s", e)
    return None


def _pixabay(query: str, w: int, h: int) -> Image.Image | None:
    if not settings.PIXABAY_API_KEY:
        return None
    try:
        r = requests.get("https://pixabay.com/api/",
                         params={"key": settings.PIXABAY_API_KEY, "q": query[:100], "image_type": "photo",
                                 "orientation": "vertical" if h > w else "horizontal", "per_page": 3,
                                 "safesearch": "true"}, timeout=20)
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if hits:
            img = requests.get(hits[0]["largeImageURL"], timeout=30)
            img.raise_for_status()
            return Image.open(BytesIO(img.content)).convert("RGB")
    except Exception as e:  # noqa: BLE001
        log.warning("Pixabay failed: %s", e)
    return None


def scene_visual(prompt: str, mood: str, w: int, h: int, workdir: Path, idx: int,
                 offline: bool = False) -> tuple[str, Path, str]:
    """Returns (kind, path, source) where kind is 'video' or 'image'."""
    key = hashlib.md5(f"{idx}:{prompt}".encode()).hexdigest()[:8]
    big = (int(w * 1.5) // 2 * 2, int(h * 1.5) // 2 * 2)
    img: Image.Image | None = None
    source = "gradient"
    if not offline:
        tried_ai = False
        for name in settings.image_order:
            if name == "pexels":
                res = _pexels(prompt, w, h, workdir, key)
                if res:
                    return res[0], res[1], "pexels"
            elif name in ("nvidia", "pollinations"):
                if tried_ai:          # generate_image() already walks nvidia -> pollinations
                    continue
                tried_ai = True
                got = generate_image(prompt, w, h)
                if got:
                    img, source = got
            elif name == "pixabay":
                img = _pixabay(prompt, w, h)
                source = "pixabay"
            if img is not None:
                break
    if img is None:
        img, source = gradient_image(*big, mood, seed=idx), "gradient"
    out = workdir / f"scene_{idx}_{key}.jpg"
    cover_resize(img, *big).save(out, "JPEG", quality=92)
    return "image", out, source
