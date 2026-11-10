"""AI image generation providers.

* nvidia       - NVIDIA API catalog (needs NVIDIA_API_KEY). Default model: FLUX.1-schnell,
                 override with NVIDIA_IMAGE_MODEL (e.g. stabilityai/stable-diffusion-3-medium).
* pollinations - free, no key. Quality / availability varies.

Stock providers (Pexels / Pixabay) live in agent/tools/visuals.py.
"""
from __future__ import annotations

import base64
import logging
from io import BytesIO

import requests
from PIL import Image

from agent.config import settings
from agent.providers.llm import _post

log = logging.getLogger(__name__)


def _decode_b64_image(b64: str) -> Image.Image:
    return Image.open(BytesIO(base64.b64decode(b64))).convert("RGB")


def _nearest_flux_size(w: int, h: int) -> tuple[int, int]:
    ratio = w / h
    if ratio < 0.8:
        return 768, 1344
    if ratio > 1.25:
        return 1344, 768
    return 1024, 1024


def _aspect_str(w: int, h: int) -> str:
    ratio = w / h
    return "9:16" if ratio < 0.8 else "16:9" if ratio > 1.25 else "1:1"


def nvidia_image(prompt: str, w: int, h: int) -> Image.Image | None:
    if not settings.NVIDIA_API_KEY:
        return None
    model = settings.NVIDIA_IMAGE_MODEL
    url = f"{settings.NVIDIA_IMAGE_URL.rstrip('/')}/{model}"
    if "flux" in model.lower():
        fw, fh = _nearest_flux_size(w, h)
        payload = {"prompt": prompt, "width": fw, "height": fh, "seed": 0, "steps": 4}
    else:  # Stable Diffusion family
        payload = {"prompt": prompt, "aspect_ratio": _aspect_str(w, h), "seed": 0, "steps": 30}
    try:
        r = _post(url, headers={"Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
                                "Accept": "application/json", "Content-Type": "application/json"},
                  payload=payload, timeout=90, retries=1)
        data = r.json()
        # The response shape differs between models; accept the known variants.
        if isinstance(data.get("artifacts"), list) and data["artifacts"]:
            return _decode_b64_image(data["artifacts"][0]["base64"])
        for key in ("image", "b64_json"):
            if isinstance(data.get(key), str):
                return _decode_b64_image(data[key])
        if isinstance(data.get("data"), list) and data["data"]:
            return _decode_b64_image(data["data"][0]["b64_json"])
        log.warning("NVIDIA image: unrecognised response keys %s", list(data))
    except Exception as e:  # noqa: BLE001
        log.warning("NVIDIA image failed: %s", e)
    return None


def pollinations_image(prompt: str, w: int, h: int) -> Image.Image | None:
    try:
        url = (f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
               f"?width={w}&height={h}&nologo=true")
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        return Image.open(BytesIO(r.content)).convert("RGB")
    except Exception as e:  # noqa: BLE001
        log.warning("Pollinations image failed: %s", e)
        return None


AI_IMAGE_PROVIDERS = {"nvidia": nvidia_image, "pollinations": pollinations_image}


def generate_image(prompt: str, w: int, h: int) -> tuple[Image.Image, str] | None:
    """Try AI image providers in the configured order. Returns (image, provider) or None."""
    for name in settings.image_order:
        fn = AI_IMAGE_PROVIDERS.get(name)
        if not fn:
            continue
        img = fn(prompt, w, h)
        if img is not None:
            return img, name
    return None
