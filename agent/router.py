"""Decide what a plain-text Telegram message wants: video, image, research or a normal answer."""
from __future__ import annotations

import re

_VIDEO = re.compile(r"\b(videos?|reels?|shorts?|clips?|vlogs?|animation)\b|वीडियो|रील", re.I)
_IMAGE = re.compile(
    r"\b(images?|photos?|pictures?|pics?|wallpapers?|posters?|logos?|illustrations?|tasveer|tasvir)\b"
    r"|तस्वीर|फोटो|इमेज", re.I)
_RESEARCH = re.compile(
    r"\b(research|search|trending|trends?|latest news|news about|find out|look up)\b|खोज", re.I)
_TRENDS = re.compile(r"\b(trending|trends)\b", re.I)


def route(text: str) -> dict:
    """Return {"intent": video|image|research|trends|ask, "payload": text}."""
    t = (text or "").strip()
    if _VIDEO.search(t):
        intent = "video"
    elif _IMAGE.search(t):
        intent = "image"
    elif _TRENDS.search(t) and len(t.split()) <= 4:
        intent = "trends"
    elif _RESEARCH.search(t):
        intent = "research"
    else:
        intent = "ask"
    return {"intent": intent, "payload": t}
