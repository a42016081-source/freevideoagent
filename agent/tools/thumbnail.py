"""Thumbnail: a frame from the video, darkened, with the title on top (rendered via libass so
Hindi titles are shaped correctly)."""
from __future__ import annotations

import logging
import textwrap
from pathlib import Path

from agent.tools.captions import font_for
from agent.tools.media import FFmpegError, run_ffmpeg

log = logging.getLogger(__name__)


def make_thumbnail(video: Path, title: str, out: Path, w: int, h: int, at_sec: float = 1.0) -> Path | None:
    """Return ``out`` (JPEG, same aspect as the video) or None if ffmpeg fails."""
    work = out.parent
    work.mkdir(parents=True, exist_ok=True)
    tw, th = (720, 1280) if h > w else (1280, 720) if w > h else (1080, 1080)
    lines = textwrap.wrap(title.strip()[:90], width=14 if h > w else 24) or [title]
    text = "\\N".join(x.replace("{", "(").replace("}", ")") for x in lines)
    fs = int(min(tw, th) * 0.11)
    ass = work / "thumb.ass"
    ass.write_text(
        f"""[Script Info]
ScriptType: v4.00+
PlayResX: {tw}
PlayResY: {th}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: T,{font_for(title)},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{max(5, fs // 9)},2,5,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:10.00,T,,0,0,0,,{text}
""", encoding="utf-8")
    vf = (f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},"
          f"eq=brightness=-0.12:saturation=1.2,subtitles={ass.name}")
    try:
        run_ffmpeg(["-ss", f"{at_sec}", "-i", str(video.resolve()), "-frames:v", "1", "-vf", vf,
                    "-q:v", "2", str(out.resolve().name)], cwd=work)
        return out if out.exists() else None
    except FFmpegError as e:
        log.warning("thumbnail failed: %s", e)
        return None
