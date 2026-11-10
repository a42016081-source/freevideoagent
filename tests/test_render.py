"""End-to-end offline render: real ffmpeg, no network."""
import json
import shutil
import subprocess

import pytest

from agent.config import settings
from agent.planner import fallback_plan
from agent.tools.video_engine import render

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="ffmpeg not installed")


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    streams = json.loads(out)["streams"]
    return {s["codec_type"]: s for s in streams}


@pytest.mark.parametrize("aspect,size", [("9:16", (1080, 1920)), ("16:9", (1920, 1080))])
def test_offline_render_makes_valid_video_with_audio(isolated, aspect, size):
    plan = fallback_plan("make a 10 second calm lofi reel in English", {"language": "en", "aspect_ratio": aspect})
    res = render(plan, isolated / "job", offline=True, scale=0.25)           # small = fast test
    st = probe(res.path)
    assert st["video"]["codec_name"] == "h264" and st["audio"]["codec_name"] == "aac"
    assert (int(st["video"]["width"]), int(st["video"]["height"])) == (size[0] // 4, size[1] // 4) or \
           (int(st["video"]["width"]) % 2 == 0)
    assert abs(res.duration - sum(res.scene_durations)) < 0.6
    assert res.has_captions and res.music == "synth"
    assert res.path.stat().st_size < 48 * 1048576


def test_custom_skill_visual_is_used(isolated):
    from PIL import Image
    img = isolated / "custom.jpg"
    Image.new("RGB", (400, 300), (200, 30, 30)).save(img)
    plan = fallback_plan("make a 8 second calm reel in English", {"language": "en"})
    res = render(plan, isolated / "job2", offline=True, scale=0.25, custom_visuals={0: ("image", img)})
    assert res.visual_sources[0] == "skill" and res.visual_sources[1] == "gradient"


def test_thumbnail_and_job_pipeline(isolated, monkeypatch):
    from agent import run_job
    monkeypatch.setattr("agent.tools.video_engine.SIZES", {"9:16": (270, 480), "16:9": (480, 270), "1:1": (360, 360)})
    monkeypatch.setenv("GITHUB_RUN_ID", "4242")
    out = run_job.run("make a 8 second calm reel in English", 0, {"language": "en", "music": False})
    assert out.exists() and (isolated / "output" / "4242_thumb.jpg").exists()
    assert "Retention check" in (isolated / "output" / "4242_report.txt").read_text(encoding="utf-8")
