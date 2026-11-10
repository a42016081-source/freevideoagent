"""Strict (but forgiving) schema for a video plan.

LLMs return slightly messy JSON, so enums and fields are coerced where that is
safe ("Hindi" -> "hi", unknown mood -> motivational) and rejected where it is
not (a plan with no scenes is invalid).
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TRANSITIONS = ("fade", "slide", "zoom", "none")


class Mood(str, Enum):
    MOTIVATIONAL = "motivational"
    SAD = "sad"
    ENERGETIC = "energetic"
    CALM = "calm"
    DRAMATIC = "dramatic"

    @classmethod
    def _missing_(cls, value):
        v = str(value).strip().lower()
        aliases = {
            "inspirational": "motivational", "epic": "motivational", "uplifting": "motivational",
            "emotional": "sad", "melancholy": "sad", "lofi": "calm", "chill": "calm",
            "relaxing": "calm", "peaceful": "calm", "hype": "energetic", "upbeat": "energetic",
            "happy": "energetic", "cinematic": "dramatic", "intense": "dramatic",
        }
        v = aliases.get(v, v)
        for m in cls:
            if m.value == v:
                return m
        return cls.MOTIVATIONAL


class AspectRatio(str, Enum):
    PORTRAIT = "9:16"
    LANDSCAPE = "16:9"
    SQUARE = "1:1"

    @classmethod
    def _missing_(cls, value):
        v = str(value).strip().lower()
        if v in ("16:9", "landscape", "horizontal", "youtube"):
            return cls.LANDSCAPE
        if v in ("1:1", "square"):
            return cls.SQUARE
        return cls.PORTRAIT


def normalize_language(value: str) -> str:
    v = str(value or "").strip().lower()
    if v.startswith("hinglish") or v in ("mixed", "roman hindi"):
        return "hinglish"
    if v.startswith("hi"):
        return "hi"
    if v.startswith("en"):
        return "en"
    return "hi"


class Scene(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int = 0
    narration: str = Field(min_length=1)
    visual_prompt: str = Field(min_length=1)
    duration_hint: float = 4.0
    transition: str = "fade"
    skill: dict | None = None      # {"name": "<skill>", "args": {...}} - the skill supplies this scene's visual

    @field_validator("skill", mode="before")
    @classmethod
    def _skill(cls, v):
        if isinstance(v, dict) and isinstance(v.get("name"), str) and v["name"].strip():
            args = v.get("args")
            return {"name": v["name"].strip(), "args": args if isinstance(args, dict) else {}}
        return None

    @field_validator("duration_hint", mode="before")
    @classmethod
    def _dur(cls, v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 4.0
        return min(max(v, 1.0), 25.0)

    @field_validator("transition", mode="before")
    @classmethod
    def _trans(cls, v):
        v = str(v or "fade").strip().lower()
        return v if v in TRANSITIONS else "fade"


class Plan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=120)
    hook: str = ""
    script: str = ""
    scenes: list[Scene] = Field(min_length=1)
    voice: str = ""
    language: Literal["hi", "en", "hinglish"] = "hi"
    music_mood: Mood = Mood.MOTIVATIONAL
    aspect_ratio: AspectRatio = AspectRatio.PORTRAIT
    caption_style: str = "karaoke"
    hashtags: list[str] = Field(default_factory=list)
    description: str = ""
    cta: str = ""
    source: str = "llm"           # which planner produced it: llm provider name / "template"
    skills_used: list[str] = Field(default_factory=list)

    @field_validator("language", mode="before")
    @classmethod
    def _lang(cls, v):
        return normalize_language(v)

    @field_validator("hook", mode="after")
    @classmethod
    def _hook(cls, v):
        return v[:90]

    @field_validator("scenes", mode="before")
    @classmethod
    def _cap_scenes(cls, v):
        # LLMs sometimes over-deliver; keep the first 12 scenes.
        return v[:12] if isinstance(v, list) else v

    @field_validator("hashtags", mode="after")
    @classmethod
    def _tags(cls, v):
        out: list[str] = []
        for t in v:
            t = "".join(str(t).split())
            if not t:
                continue
            t = t if t.startswith("#") else f"#{t}"
            if t not in out:
                out.append(t)
        return out

    @model_validator(mode="after")
    def _finish(self):
        for i, s in enumerate(self.scenes):
            s.index = i
        if not self.script.strip():
            self.script = " ".join(s.narration for s in self.scenes)
        if not self.hook:
            self.hook = self.scenes[0].narration[:90]
        return self

    @property
    def total_hint(self) -> float:
        return sum(s.duration_hint for s in self.scenes)
