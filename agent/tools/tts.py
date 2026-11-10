"""Text-to-speech, one scene at a time (so audio and video stay perfectly in sync).

Provider order:  ElevenLabs (if key) -> edge-tts (free) -> espeak-ng (offline) -> silence.
The silent fallback means a video is always produced, even with no network.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import requests

from agent.config import settings
from agent.tools.media import SR, probe_duration, to_wav, write_wav

log = logging.getLogger(__name__)


@dataclass
class Speech:
    path: Path                      # 16-bit mono WAV
    duration: float
    provider: str                   # elevenlabs | edge | espeak | silence
    words: list[tuple[str, float, float]] = field(default_factory=list)  # (word, start, end) in seconds


def clean_for_tts(text: str) -> str:
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[#*_~`<>|\\]+", " ", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", " ", text)   # emoji
    return re.sub(r"\s+", " ", text).strip()


def estimate_duration(text: str) -> float:
    return max(1.5, len(text.split()) * 0.42)


def word_timings(text: str, duration: float, lead: float = 0.05, tail: float = 0.12) -> list[tuple[str, float, float]]:
    """Spread words over the spoken duration proportionally to their length.

    Not sample-accurate, but within one scene (a sentence or two) it is close enough that
    karaoke highlighting looks natural, and it needs no model or network."""
    words = text.split()
    if not words:
        return []
    span = max(duration - lead - tail, 0.3)
    weights = [len(w) + 2 for w in words]
    total = float(sum(weights))
    out, t = [], lead
    for w, wt in zip(words, weights):
        d = span * wt / total
        out.append((w, round(t, 3), round(t + d, 3)))
        t += d
    return out


async def _edge_save(text: str, voice: str, rate: str, out: Path) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))


def _edge(text: str, voice: str, rate: str, out: Path) -> bool:
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        log.warning("edge-tts is not installed")
        return False
    for attempt in range(3):
        try:
            asyncio.run(_edge_save(text, voice, rate, out))
            if out.exists() and out.stat().st_size > 500:
                return True
        except Exception as e:  # noqa: BLE001
            log.warning("edge-tts attempt %d failed: %s", attempt + 1, e)
        time.sleep(1.5 * (attempt + 1))
    return False


def _elevenlabs(text: str, out: Path) -> bool:
    if not settings.ELEVENLABS_API_KEY:
        return False
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{settings.ELEVENLABS_VOICE_ID}",
            headers={"xi-api-key": settings.ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_multilingual_v2",
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=90)
        r.raise_for_status()
        out.write_bytes(r.content)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("ElevenLabs failed: %s", e)
        return False


def _espeak(text: str, voice: str, out_wav: Path) -> bool:
    exe = shutil.which("espeak-ng")
    if not exe:
        return False
    lang = "hi" if voice.lower().startswith("hi") else "en"
    try:
        subprocess.run([exe, "-v", lang, "-s", "150", "-w", str(out_wav), "--", text],
                       check=True, capture_output=True, timeout=60)
        return out_wav.exists() and out_wav.stat().st_size > 500
    except Exception as e:  # noqa: BLE001
        log.warning("espeak-ng failed: %s", e)
        return False


def synthesize(text: str, voice: str, out_wav: Path, rate: str = "+0%", offline: bool = False) -> Speech:
    """Speak ``text`` into ``out_wav`` (WAV). Never raises; falls back to silence."""
    text = clean_for_tts(text) or "..."
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    tmp_mp3 = out_wav.with_suffix(".src.mp3")
    tmp_wav = out_wav.with_suffix(".src.wav")
    provider = ""
    try:
        if not offline and _elevenlabs(text, tmp_mp3):
            to_wav(tmp_mp3, out_wav)
            provider = "elevenlabs"
        elif not offline and _edge(text, voice, rate, tmp_mp3):
            to_wav(tmp_mp3, out_wav)
            provider = "edge"
        elif _espeak(text, voice, tmp_wav):
            to_wav(tmp_wav, out_wav)
            provider = "espeak"
    except Exception as e:  # noqa: BLE001
        log.warning("TTS conversion failed: %s", e)
        provider = ""
    finally:
        tmp_mp3.unlink(missing_ok=True)
        tmp_wav.unlink(missing_ok=True)

    if provider:
        dur = probe_duration(out_wav) or estimate_duration(text)
    else:
        dur = estimate_duration(text)
        write_wav(out_wav, np.zeros(int(SR * dur), dtype=np.float32))
        provider = "silence"
    return Speech(out_wav, dur, provider, word_timings(text, dur))
