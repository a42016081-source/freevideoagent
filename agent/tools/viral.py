"""Viral optimizer: title variants, retention checklist, hashtags, posting time."""
from __future__ import annotations

import logging

from agent.config import settings
from agent.planner.schema import Plan
from agent.providers.llm import complete, extract_json

log = logging.getLogger(__name__)

_MOOD_TAGS = {
    "motivational": ["#motivation", "#mindset", "#success", "#hustle", "#dailymotivation"],
    "sad": ["#sad", "#emotional", "#feelings", "#quotes", "#heartbreak"],
    "energetic": ["#viral", "#energy", "#trending", "#reels", "#fyp"],
    "calm": ["#lofi", "#calm", "#aesthetic", "#chill", "#relax"],
    "dramatic": ["#cinematic", "#story", "#drama", "#shorts", "#fyp"],
}


def ab_titles(plan: Plan, n: int = 3) -> list[str]:
    """Alternative titles for A/B testing (LLM when reachable, heuristics otherwise)."""
    if not settings.RENDER_OFFLINE:
        try:
            raw = complete(
                f"You are a viral short-video strategist. Write {n} alternative titles for this video, "
                f"in the same language as the original title. Return ONLY a JSON array of strings.",
                f"Title: {plan.title}\nHook: {plan.hook}\nScript: {plan.script[:600]}",
                timeout=30)
            arr = extract_json(raw)
            titles = [str(x).strip()[:100] for x in arr if str(x).strip()]
            if titles:
                return titles[:n]
        except Exception as e:  # noqa: BLE001
            log.info("A/B titles via LLM skipped: %s", e)
    return [plan.title, f"{plan.hook[:70]}", f"{plan.title} - dekhna zaroor"][:n]


def retention_report(plan: Plan, duration: float, has_captions: bool) -> str:
    first = plan.scenes[0].duration_hint if plan.scenes else 99
    checks = [
        (bool(plan.hook) and len(plan.hook) <= 90, "Hook present (<= 90 chars)"),
        (15 <= duration <= 45, f"Length {duration:.0f}s (sweet spot 15-45s)"),
        (has_captions, "Animated captions"),
        (bool(plan.cta.strip()), "Call-to-action / loopable ending"),
        (len(plan.hashtags) >= 3, f"{len(plan.hashtags)} hashtags"),
        (3 <= len(plan.scenes) <= 8, f"{len(plan.scenes)} scenes (3-8 keeps pace fast)"),
        (first <= 6, "First scene short (hook lands fast)"),
    ]
    return "\n".join(f"{'✅' if ok else '⚠️'} {label}" for ok, label in checks)


def best_posting_time(language: str) -> str:
    if language in ("hi", "hinglish"):
        return "Best time (IST): 7-9 AM, 12-2 PM or 8-11 PM. Tue/Wed/Thu usually do best."
    return "Best time (your local time): 6-9 AM, 12-1 PM or 7-10 PM. Tue/Thu usually do best."


def optimize_hashtags(plan: Plan, n: int = 8) -> list[str]:
    seen: list[str] = []
    for t in list(plan.hashtags) + _MOOD_TAGS.get(plan.music_mood.value, []):
        tag = t if t.startswith("#") else f"#{t}"
        if tag.lower() not in [s.lower() for s in seen]:
            seen.append(tag)
    return seen[:n]
