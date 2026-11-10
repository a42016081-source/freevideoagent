"""Which optional providers are switched on (never exposes the keys)."""
from __future__ import annotations

import shutil

from agent import d1
from agent.config import settings


def enabled_providers() -> dict[str, bool]:
    return {
        "nvidia_llm": bool(settings.NVIDIA_API_KEY),
        "nvidia_image": bool(settings.NVIDIA_API_KEY),
        "openai_compatible_llm": bool(settings.OPENAI_BASE_URL and settings.OPENAI_API_KEY),
        "cloudflare_d1_memory": d1.enabled(),
        "pollinations (free)": True,
        "elevenlabs_tts": bool(settings.ELEVENLABS_API_KEY),
        "edge_tts (free)": True,
        "espeak_offline_tts": shutil.which("espeak-ng") is not None,
        "pexels_stock": bool(settings.PEXELS_API_KEY),
        "pixabay_stock": bool(settings.PIXABAY_API_KEY),
    }
