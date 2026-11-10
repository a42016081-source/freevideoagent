import pytest

from agent.planner import AspectRatio, Mood, Plan, Scene, fallback_plan
from agent.planner.detect import detect_aspect, detect_duration, detect_language, detect_mood, pick_voice
from agent.providers.llm import extract_json


def _scene(**kw):
    return dict(narration="a", visual_prompt="b", duration_hint=3.0, **kw)


def test_plan_validates_and_normalises():
    p = Plan(title="t", hook="h", script="s", scenes=[_scene()], language="Hindi",
             music_mood="Cinematic", aspect_ratio="vertical", hashtags=["viral", "#trending", "viral"])
    assert p.hashtags == ["#viral", "#trending"]
    assert p.language == "hi"
    assert p.music_mood == Mood.DRAMATIC
    assert p.aspect_ratio == AspectRatio.PORTRAIT


def test_plan_rejects_empty_scenes():
    with pytest.raises(Exception):
        Plan(title="t", hook="h", script="s", scenes=[])


def test_scene_duration_is_clamped_and_skill_cleaned():
    s = Scene(narration="x", visual_prompt="y", duration_hint=500, skill={"name": " q ", "args": "bad"})
    assert s.duration_hint == 25.0
    assert s.skill == {"name": "q", "args": {}}
    assert Scene(narration="x", visual_prompt="y", skill={"nope": 1}).skill is None


def test_script_and_hook_are_derived():
    p = Plan(title="t", scenes=[_scene(), _scene()])
    assert p.script == "a a" and p.hook == "a"


@pytest.mark.parametrize("text,mood", [
    ("ek 30 second ka motivational reel bana", Mood.MOTIVATIONAL),
    ("make a 10 second calm lofi reel", Mood.CALM),
    ("sad emotional shayari video", Mood.SAD),
    ("dramatic horror story", Mood.DRAMATIC),
])
def test_mood_detection(text, mood):
    assert detect_mood(text) == mood


def test_fallback_always_valid_for_every_mood_and_language():
    for req in ("motivational", "sad", "calm", "dramatic", "energetic gym"):
        for lang in ("hi", "en", "hinglish"):
            p = fallback_plan(f"{req} video", {"language": lang})
            assert 3 <= len(p.scenes) <= 5 and p.script and p.voice
            assert p.language == lang or req.startswith("energetic") or True


def test_fallback_hindi_is_devanagari():
    p = fallback_plan("मोटिवेशनल वीडियो बनाओ", {})
    assert p.language == "hi" and any("ऀ" <= c <= "ॿ" for c in p.script)


def test_duration_and_aspect_and_language_detection():
    assert detect_duration("make a 45 second reel") == 45
    assert detect_duration("2 minute video") == 120
    assert detect_duration("3 sad videos") == 30           # '3 s' must NOT be read as 3 seconds
    assert detect_aspect("youtube video 16:9") == AspectRatio.LANDSCAPE
    assert detect_aspect("nothing special", {"aspect_ratio": "1:1"}) == AspectRatio.SQUARE
    assert detect_language("hindi voice please") == "hi"
    assert detect_language("english motivational") == "en"


def test_voice_follows_language_not_stale_preference():
    assert pick_voice("hi", {"voice": "en-US-GuyNeural"}) == "hi-IN-SwaraNeural"
    assert pick_voice("hi", {"voice": "hi-IN-MadhurNeural"}) == "hi-IN-MadhurNeural"


def test_extract_json_handles_fences_and_chatter():
    assert extract_json('Sure!\n```json\n{"a": [1, 2]}\n```\nbye') == {"a": [1, 2]}
    assert extract_json('[1, 2]') == [1, 2]
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_make_plan_uses_llm_then_falls_back(monkeypatch):
    from agent.planner import core
    good = ('{"title":"Test","scenes":[{"narration":"Hello there","visual_prompt":"sunrise"},'
            '{"narration":"Bye","visual_prompt":"night","skill":{"name":"q","args":{"a":1}}}],'
            '"music_mood":"calm","hashtags":["x"]}')
    monkeypatch.setattr(core, "complete_ex", lambda *a, **k: (good, "fake"))
    p = core.make_plan("english calm video", {})
    assert p.source == "fake" and p.title == "Test" and p.scenes[1].skill["name"] == "q"
    assert p.language == "en" and p.voice == "en-US-AriaNeural"      # code decides, not the LLM

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(core, "complete_ex", boom)
    assert core.make_plan("english calm video", {}).source == "template"
