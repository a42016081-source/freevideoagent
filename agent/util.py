"""Small shared helpers."""
from __future__ import annotations

import html
import re


def esc(text: object) -> str:
    """HTML-escape for Telegram parse_mode=HTML."""
    return html.escape(str(text), quote=False)


def shorten(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: max(0, n - 1)].rstrip() + "…"


def has_devanagari(text: str) -> bool:
    return bool(re.search(r"[ऀ-ॿ]", text or ""))


def slugify(name: str, default: str = "item") -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", (name or "").lower().replace("-", "_")).strip("_")
    return s[:48] or default
