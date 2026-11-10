"""Cheap, deterministic request analysis (language, mood, aspect ratio, length, voice)."""
from __future__ import annotations

import re

from agent.config import cfg
from agent.planner.schema import AspectRatio, Mood
from agent.util import has_devanagari

_HINGLISH_WORDS = {"ka", "ki", "ke", "hai", "bana", "banao", "bnao", "ek", "chahiye", "wala",
                   "wali", "karo", "kar", "mujhe", "mere", "me", "mein", "aur", "nahi", "kaise"}


def detect_language(text: str, prefs: dict | None = None) -> str:
    """Priority: explicit language words > Devanagari script > saved preference > default."""
    t = (text or "").lower()
    prefs = prefs or {}
    if "hinglish" in t:
        return "hinglish"
    wants_en = re.search(r"\benglish\b|\bangrezi\b|अंग्रेज", t)
    wants_hi = re.search(r"\bhindi\b|हिंदी|हिन्दी", t)
    if wants_hi and not wants_en:
        return "hi"
    if wants_en and not wants_hi:
        return "en"
    if has_devanagari(text):
        return "hi"
    words = set(re.findall(r"[a-z]+", t))
    if len(words & _HINGLISH_WORDS) >= 2:
        return "hi"   # Roman-Hindi request -> Devanagari narration reads best with hi-IN voices
    return prefs.get("language") or cfg("defaults.language", "hi")


def detect_mood(text: str) -> Mood:
    t = (text or "").lower()
    rules = [
        (Mood.SAD, ("sad", "emotional", "dukhi", "heartbreak", "उदास", "दर्द")),
        (Mood.CALM, ("calm", "relax", "lofi", "lo-fi", "peace", "शांत")),
        (Mood.ENERGETIC, ("energetic", "hype", "fast", "fitness", "gym", "workout")),
        (Mood.DRAMATIC, ("dramatic", "intense", "cinematic", "horror", "thriller", "story")),
        (Mood.MOTIVATIONAL, ("motivat", "inspir", "success", "प्रेरणा", "मोटिवेश")),
    ]
    for mood, words in rules:
        if any(w in t for w in words):
            return mood
    return Mood.MOTIVATIONAL


def detect_aspect(text: str, prefs: dict | None = None) -> AspectRatio:
    t = (text or "").lower()
    if "16:9" in t or "landscape" in t or "youtube video" in t:
        return AspectRatio.LANDSCAPE
    if "1:1" in t or "square" in t:
        return AspectRatio.SQUARE
    if "9:16" in t or "reel" in t or "short" in t or "vertical" in t:
        return AspectRatio.PORTRAIT
    return AspectRatio((prefs or {}).get("aspect_ratio") or cfg("defaults.aspect_ratio", "9:16"))


def detect_duration(text: str, default: float = 30.0) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|sec|s\b|सेकंड|सेकेंड|minutes?|mins?|min\b|मिनट)",
                  text or "", re.I)
    if not m:
        return default
    n = float(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith(("min", "मिनट")):
        n *= 60
    return min(max(n, 8.0), 120.0)


def pick_voice(language: str, prefs: dict | None = None) -> str:
    prefs = prefs or {}
    v = prefs.get("voice")
    if v:
        prefix = v[:2].lower()
        # a saved voice is only used when it matches the language of this video
        if (language == "hi" and prefix == "hi") or (language in ("en", "hinglish") and prefix == "en"):
            return v
    if language == "hi":
        return cfg("defaults.voice_hi", "hi-IN-SwaraNeural")
    if language == "hinglish":
        return cfg("defaults.voice_hinglish", "en-IN-NeerjaNeural")
    return cfg("defaults.voice_en", "en-US-AriaNeural")


def voice_rate(mood: Mood) -> str:
    return {"energetic": "+10%", "dramatic": "-5%", "sad": "-8%", "calm": "-5%"}.get(mood.value, "+0%")
