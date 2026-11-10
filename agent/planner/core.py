"""LLM planner: natural-language request -> validated Plan."""
from __future__ import annotations

import json
import logging

from agent.planner.detect import (detect_aspect, detect_duration, detect_language, detect_mood,
                                  pick_voice)
from agent.planner.fallback import fallback_plan
from agent.planner.schema import Plan
from agent.providers.llm import complete_ex, extract_json

log = logging.getLogger(__name__)

SYSTEM = """You are the planner of a short-video agent. Turn the user's request into ONE strict JSON plan.
Return ONLY the JSON object, no markdown, no commentary.

Shape:
{{
  "title": str,
  "hook": str,                      // <= 90 chars, spoken in the first 2 seconds, creates a curiosity gap
  "script": str,
  "scenes": [{{"narration": str, "visual_prompt": str, "duration_hint": float,
              "transition": "fade|slide|zoom|none"}}],
  "music_mood": "motivational|sad|energetic|calm|dramatic",
  "hashtags": [str],                // 5-8 relevant tags
  "description": str,               // keyword-rich caption for the platform
  "cta": str
}}

Rules:
- Language of "narration": {lang_rule}
- "visual_prompt" is ALWAYS English: a concrete, filmable, cinematic image description (no text in the image).
- Target total length: about {seconds:.0f} seconds => {n_scenes} scenes, roughly {words} spoken words in total.
- Viral structure: scene 1 is a punchy hook; short sentences (max 15 words); build tension; the last
  scene is a loopable ending + call to action.
- Never write text that needs reading aloud symbols, emojis, hashtags or URLs in "narration".
{extra}"""

_LANG_RULES = {
    "hi": "Hindi in Devanagari script (e.g. 'हर दिन एक नई शुरुआत है।'). No Roman letters.",
    "hinglish": "Hindi written in Roman/English letters (Hinglish), natural spoken style.",
    "en": "English.",
}


def build_system(lang: str, seconds: float, extra: str = "") -> str:
    n_scenes = min(8, max(3, round(seconds / 5)))
    words = int(seconds * (2.0 if lang == "en" else 2.2))
    return SYSTEM.format(lang_rule=_LANG_RULES[lang], seconds=seconds, n_scenes=n_scenes,
                         words=words, extra=extra)


def make_plan(user_request: str, prefs: dict | None = None, skills_context: str = "") -> Plan:
    prefs = prefs or {}
    lang = detect_language(user_request, prefs)
    seconds = detect_duration(user_request)
    mood_hint = detect_mood(user_request)
    aspect = detect_aspect(user_request, prefs)

    extra = ""
    if prefs.get("notes"):
        extra += "\nThings to always remember about this user (follow them): " + \
                 "; ".join(str(n)[:200] for n in prefs["notes"][-10:])
    if prefs.get("feedback"):
        extra += "\nUser's recent feedback on earlier videos (apply it): " + \
                 "; ".join(prefs["feedback"][-5:])
    if skills_context:
        extra += "\n\nInstalled skills the user taught you (follow their instructions when relevant):\n" \
                 + skills_context

    system = build_system(lang, seconds, extra)
    user = user_request
    last_error = ""
    for attempt in (1, 2):
        try:
            prompt = user if not last_error else (
                f"{user}\n\nYour previous answer was invalid ({last_error}). Return valid JSON only.")
            raw, provider = complete_ex(system, prompt, temperature=0.8, timeout=90)
            data = extract_json(raw)
            if isinstance(data, list):
                data = data[0]
            plan = Plan(**data)
            # Code (not the LLM) decides language / voice / aspect, so they always match the request.
            plan.language = lang
            plan.voice = pick_voice(lang, prefs)
            plan.aspect_ratio = aspect
            if mood_hint.value != "motivational" or "music_mood" not in data:
                plan.music_mood = mood_hint
            plan.source = provider
            return plan
        except Exception as e:  # noqa: BLE001
            last_error = str(e)[:300]
            log.warning("planner attempt %d failed: %s", attempt, last_error)
    log.warning("falling back to template planner")
    return fallback_plan(user_request, prefs)


def plan_to_json(plan: Plan) -> str:
    return json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
