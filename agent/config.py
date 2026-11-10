"""Central settings.

* Secrets / provider keys come from environment variables (or a local .env).
* Defaults and render tuning come from config.yaml.
* Empty-string env vars are treated as "not set" (GitHub Actions passes unset
  secrets as empty strings).
* Always read ``settings.X`` at call time (never cache it in a module constant),
  so tests can override paths.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _load_config() -> dict:
    try:
        return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


CONFIG: dict = _load_config()


def cfg(path: str, default=None):
    """Read a dotted path from config.yaml, e.g. cfg('render.fps', 24)."""
    cur = CONFIG
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


def _env_ids(name: str) -> list[int]:
    out = []
    for x in _env(name).split(","):
        x = x.strip()
        if x.lstrip("-").isdigit():
            out.append(int(x))
    return out


class Settings:
    def __init__(self) -> None:
        # --- required for the bot ---
        self.TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
        self.GITHUB_TOKEN = _env("GITHUB_TOKEN")
        self.GITHUB_REPO = _env("GITHUB_REPO") or _env("GITHUB_REPOSITORY")
        self.GITHUB_BRANCH = _env("DEFAULT_BRANCH") or _env("GITHUB_REF_NAME") or "main"
        # Fail closed: nobody is allowed unless listed.
        self.ALLOWED_USER_IDS = _env_ids("ALLOWED_USER_IDS")

        # --- optional LLM providers (all OpenAI-compatible) ---
        self.NVIDIA_API_KEY = _env("NVIDIA_API_KEY")
        self.NVIDIA_BASE_URL = _env("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
        self.NVIDIA_MODEL = _env("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
        self.NVIDIA_IMAGE_MODEL = _env("NVIDIA_IMAGE_MODEL", "black-forest-labs/flux.1-schnell")
        self.NVIDIA_IMAGE_URL = _env("NVIDIA_IMAGE_URL", "https://ai.api.nvidia.com/v1/genai")

        self.OPENAI_BASE_URL = _env("OPENAI_BASE_URL")
        self.OPENAI_API_KEY = _env("OPENAI_API_KEY")
        self.OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4o-mini")

        self.OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434")
        self.OLLAMA_MODEL = _env("OLLAMA_MODEL", "qwen2.5:3b")

        # --- optional voice / media ---
        self.ELEVENLABS_API_KEY = _env("ELEVENLABS_API_KEY")
        self.ELEVENLABS_VOICE_ID = _env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
        self.PEXELS_API_KEY = _env("PEXELS_API_KEY")
        self.PIXABAY_API_KEY = _env("PIXABAY_API_KEY")

        # --- optional permanent memory: Cloudflare D1 (all three needed) ---
        self.CLOUDFLARE_ACCOUNT_ID = _env("CLOUDFLARE_ACCOUNT_ID")
        self.D1_DATABASE_ID = _env("D1_DATABASE_ID")
        self.CLOUDFLARE_API_TOKEN = _env("CLOUDFLARE_API_TOKEN")
        self.CLOUDFLARE_API_BASE = _env("CLOUDFLARE_API_BASE", "https://api.cloudflare.com/client/v4")

        # --- optional web chat (Cloudflare Worker in web/): where the bot sends answers, and the shared secret ---
        self.WEB_BASE_URL = _env("WEB_BASE_URL").rstrip("/")
        self.BRIDGE_SECRET = _env("BRIDGE_SECRET")

        # --- runtime ---
        self.LOG_LEVEL = _env("LOG_LEVEL", "INFO")
        self.RENDER_OFFLINE = _env("RENDER_OFFLINE") in ("1", "true", "yes")
        self.KEEP_WORK = _env("KEEP_WORK") in ("1", "true", "yes")
        self.MAX_UPLOAD_MB = float(cfg("render.max_size_mb", 48))

        self.OUTPUT_DIR = Path(_env("FVA_OUTPUT_DIR") or ROOT / "output")
        self.SKILLS_DIR = Path(_env("FVA_SKILLS_DIR") or ROOT / "skills")
        self.STATE_DIR = Path(_env("FVA_STATE_DIR") or ROOT / "state")
        self.ASSETS_DIR = Path(_env("FVA_ASSETS_DIR") or ROOT / "assets")

    @property
    def llm_order(self) -> list[str]:
        order = _env("LLM_ORDER")
        if order:
            return [x.strip() for x in order.split(",") if x.strip()]
        return list(cfg("providers.llm_order", ["nvidia", "openai", "pollinations", "ollama"]))

    @property
    def image_order(self) -> list[str]:
        order = _env("IMAGE_ORDER")
        if order:
            return [x.strip() for x in order.split(",") if x.strip()]
        return list(cfg("providers.image_order", ["pexels", "nvidia", "pollinations", "pixabay"]))


settings = Settings()
