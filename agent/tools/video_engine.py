"""Video renderer built on plain ffmpeg (no MoviePy / ImageMagick).

Pipeline
  1. Voice-over per scene (TTS) -> exact scene lengths, so picture and sound never drift apart.
  2. Visual per scene (stock video / AI image / gradient) -> Ken Burns clip.
  3. Clips joined with xfade transitions.
  4. Audio: voice + music (auto-ducked) + whoosh at every cut, mixed in numpy.
  5. One final encode: colour grade + burned karaoke captions (ASS) + audio, sized to fit Telegram.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from agent.config import cfg, settings
from agent.planner.detect import voice_rate
from agent.planner.schema import Plan
from PIL import Image

from agent.tools import audio_mix, captions, music, sfx, tts, visuals
from agent.tools.media import (FFmpegError, SR, probe_duration, read_wav, run_ffmpeg,
                               write_wav)

log = logging.getLogger(__name__)

SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
XFADE = {"fade": "fade", "slide": "slideleft", "zoom": "zoomin", "none": "fade"}


@dataclass
class RenderResult:
    path: Path
    duration: float
    scene_durations: list[float]
    has_captions: bool
    tts_providers: list[str] = field(default_factory=list)
    visual_sources: list[str] = field(default_factory=list)
    music: str = "none"


def _even(x: float) -> int:
    return max(int(x) // 2 * 2, 2)


# ---------------------------------------------------------------- scene clips
def _render_clip(kind: str, src: Path, out: Path, w: int, h: int, fps: int, dur: float, idx: int) -> None:
    frames = int(math.ceil(dur * fps)) + 1
    enc = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "17", "-pix_fmt", "yuv420p", "-an"]
    if kind == "video":
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
              f"fps={fps},setsar=1,format=yuv420p")
        run_ffmpeg(["-stream_loop", "-1", "-i", str(src), "-t", f"{dur:.3f}", "-vf", vf, *enc, str(out)])
        return
    if cfg("render.kenburns", True):
        mode = idx % 3          # zoom in / zoom out / slow pan
        z = ["1+0.12*on/{n}", "1.12-0.12*on/{n}", "1.1"][mode].format(n=frames)
        x = "(iw-iw/zoom)/2" if mode < 2 else "(iw-iw/zoom)*on/{n}".format(n=frames)
        vf = (f"zoompan=z='{z}':x='{x}':y='(ih-ih/zoom)/2':d={frames}:s={w}x{h}:fps={fps},"
              f"setsar=1,format=yuv420p")
        run_ffmpeg(["-i", str(src), "-vf", vf, "-frames:v", str(frames), *enc, str(out)])
    else:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p"
        run_ffmpeg(["-loop", "1", "-framerate", str(fps), "-i", str(src), "-t", f"{dur:.3f}",
                    "-vf", vf, *enc, str(out)])


def _join_clips(clips: list[Path], L: list[float], d: list[float], trans: list[str], out: Path,
                workdir: Path, fps: int) -> None:
    """Join scene clips. Clip i is L[i] + d[i] long (it overlaps the next scene by d[i])."""
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    if len(clips) == 1:
        run_ffmpeg(["-i", str(clips[0]), *enc, str(out)])
        return
    try:
        inputs: list[str] = []
        for c in clips:
            inputs += ["-i", str(c)]
        graph, prev, cum = [], "[0:v]", 0.0
        for k in range(len(clips) - 1):
            cum += L[k]
            label = f"[v{k}]"
            graph.append(f"{prev}[{k + 1}:v]xfade=transition={XFADE.get(trans[k], 'fade')}:"
                         f"duration={d[k]:.3f}:offset={cum:.3f}{label}")
            prev = label
        run_ffmpeg([*inputs, "-filter_complex", ";".join(graph), "-map", prev, *enc, str(out)])
    except FFmpegError as e:
        log.warning("xfade join failed (%s); falling back to hard cuts", e)
        lst = workdir / "concat.txt"
        lst.write_text("".join(f"file '{c.resolve()}'\noutpoint {L[i]:.3f}\n" for i, c in enumerate(clips)))
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), *enc, str(out)])


# ------------------------------------------------------------------- final
def _final_encode(video: Path, audio: Path, out: Path, workdir: Path, total: float, *,
                  ass: Path | None, crf: int, preset: str, kbps: int | None) -> None:
    vf = "eq=contrast=1.05:saturation=1.10"
    if ass:
        vf += f",subtitles={ass.name}"
        fonts = [p for p in (settings.ASSETS_DIR / "fonts").glob("*") if p.suffix.lower() in (".ttf", ".otf")]
        if fonts:
            vf += f":fontsdir='{(settings.ASSETS_DIR / 'fonts').resolve()}'"
    rate = ["-crf", str(crf)] if not kbps else [
        "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.3)}k", "-bufsize", f"{kbps * 2}k"]
    run_ffmpeg(["-i", video.name, "-i", audio.name, "-map", "0:v", "-map", "1:a", "-vf", vf,
                "-c:v", "libx264", "-preset", preset, *rate, "-pix_fmt", "yuv420p",
                "-threads", str(cfg("render.threads", 2)),
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-t", f"{total:.3f}",
                out.name], cwd=workdir)


def render(plan: Plan, workdir: Path, *, offline: bool | None = None, watermark: str | None = None,
           scale: float = 1.0, use_music: bool = True, with_captions: bool = True,
           preset: str | None = None, progress: Callable[[str], None] | None = None,
           custom_visuals: dict[int, tuple[str, Path]] | None = None) -> RenderResult:
    say = progress or (lambda _m: None)
    offline = settings.RENDER_OFFLINE if offline is None else offline
    workdir.mkdir(parents=True, exist_ok=True)
    w0, h0 = SIZES[plan.aspect_ratio.value]
    w, h = _even(w0 * scale), _even(h0 * scale)
    fps = int(cfg("render.fps", 24))
    n = len(plan.scenes)
    mood = plan.music_mood.value
    rate = voice_rate(plan.music_mood)

    choice = music.resolve(mood) if use_music else None
    grid = (60.0 / choice.bpm) / 2 if choice and choice.bpm else None   # snap cuts to half-beats
    pad = float(cfg("render.scene_pad", 0.35))

    # 1) voice per scene -> scene lengths
    speeches: list[tts.Speech] = []
    L: list[float] = []
    for i, sc in enumerate(plan.scenes):
        sp = tts.synthesize(sc.narration, plan.voice, workdir / f"voice_{i}.wav", rate, offline)
        speeches.append(sp)
        d = max(sp.duration + (pad * 2 if i == n - 1 else pad), 1.8)
        if grid:
            d = math.ceil(d / grid - 1e-6) * grid
        L.append(d)
        say(f"🎙 Voice {i + 1}/{n} ({sp.provider})")
    total = sum(L)
    starts = [sum(L[:i]) for i in range(n)]

    # 2) visuals -> clips
    base_d = min(float(cfg("render.transition_sec", 0.3)), 0.4 * min(L))
    trans = [plan.scenes[i].transition for i in range(n)]
    d = [0.05 if trans[i] == "none" else base_d for i in range(max(n - 1, 0))]
    clips, sources = [], []
    for i, sc in enumerate(plan.scenes):
        if custom_visuals and i in custom_visuals:          # picture/video produced by a skill
            kind, src = custom_visuals[i]
            source = "skill"
            if kind == "image":
                big = (int(w * 1.5) // 2 * 2, int(h * 1.5) // 2 * 2)
                prepared = workdir / f"custom_{i}.jpg"
                visuals.cover_resize(Image.open(src).convert("RGB"), *big).save(prepared, "JPEG", quality=92)
                src = prepared
        else:
            kind, src, source = visuals.scene_visual(sc.visual_prompt, mood, w, h, workdir, i, offline)
        sources.append(source)
        clip = workdir / f"clip_{i}.mp4"
        dur = L[i] + (d[i] if i < n - 1 else 0.0) + 1.0 / fps
        _render_clip(kind, src, clip, w, h, fps, dur, i)
        clips.append(clip)
        say(f"🖼 Visual {i + 1}/{n} ({source})")
    silent = workdir / "video_silent.mp4"
    _join_clips(clips, L, d, trans, silent, workdir, fps)
    say("🎞 Scenes joined")

    # 3) audio
    voice = np.zeros(int(total * SR) + SR, dtype=np.float32)
    words: list[captions.Word] = []
    for i, sp in enumerate(speeches):
        x = read_wav(sp.path)
        s0 = int(starts[i] * SR)
        voice[s0:s0 + len(x)] += x[: len(voice) - s0]
        words += [(wd, starts[i] + ws, starts[i] + we) for wd, ws, we in sp.words]
    music_arr, music_label = None, "none"
    if choice:
        try:
            mpath = music.prepare(choice, total, workdir / "music.wav")
            music_arr = read_wav(mpath)
            music_label = choice.kind
        except Exception as e:  # noqa: BLE001
            log.warning("music failed, continuing without: %s", e)
    events = [(max(starts[i] - 0.12, 0.0), sfx.whoosh(), 0.35) for i in range(1, n)]
    events.append((0.05, sfx.pop(), 0.25))
    mixed = audio_mix.mix(voice[: int(total * SR)], total, music_arr, events)
    audio = write_wav(workdir / "mix.wav", mixed)
    say("🎚 Audio mixed")

    # 4) captions
    ass = None
    if with_captions and words:
        if os.getenv("CAPTIONS_ALIGN", "").lower() == "whisper":
            vtrack = write_wav(workdir / "voice_track.wav", voice[: int(total * SR)])
            aligned = captions.whisper_words(vtrack, plan.language if plan.language in ("hi", "en") else None)
            if aligned:
                words = aligned
        ass = captions.build_ass(words, w, h, workdir / "captions.ass", watermark=watermark, total=total)

    # 5) final encode (+ shrink to fit Telegram if needed)
    final = workdir / "final.mp4"
    preset = preset or str(cfg("render.preset", "medium"))
    crf = int(cfg("render.crf", 23))
    say("🎬 Final render…")
    _final_encode(silent, audio, final, workdir, total, ass=ass, crf=crf, preset=preset, kbps=None)
    max_mb = settings.MAX_UPLOAD_MB
    size_mb = final.stat().st_size / 1048576
    if size_mb > max_mb:
        kbps = max(int((max_mb * 8192 / total) * 0.9) - 128, 300)
        log.info("%.1f MB > %.0f MB; re-encoding at %d kbps", size_mb, max_mb, kbps)
        _final_encode(silent, audio, final, workdir, total, ass=ass, crf=crf, preset=preset, kbps=kbps)

    return RenderResult(final, probe_duration(final) or total, L, ass is not None,
                        [s.provider for s in speeches], sources, music_label)
