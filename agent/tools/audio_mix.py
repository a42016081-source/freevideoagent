"""Final audio mix: voice + music (auto-ducked under the voice) + SFX at the cuts."""
from __future__ import annotations

import numpy as np

from agent.tools.media import SR


def _smooth(x: np.ndarray, seconds: float) -> np.ndarray:
    win = max(int(SR * seconds), 1)
    return np.convolve(x, np.ones(win, dtype=np.float32) / win, mode="same")


def duck_gain(voice: np.ndarray, depth: float = 0.75) -> np.ndarray:
    """Gain curve for the music: ~1.0 while nobody talks, (1 - depth) while the voice is present."""
    env = _smooth(np.abs(voice), 0.08)
    presence = np.clip(env / 0.02, 0.0, 1.0)
    presence = _smooth(presence, 0.30)                       # slow release -> no pumping
    return (1.0 - depth * presence).astype(np.float32)


def _fit(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n]
    return np.pad(x, (0, n - len(x)))


def mix(voice: np.ndarray, total_sec: float, music: np.ndarray | None = None,
        sfx: list[tuple[float, np.ndarray, float]] | None = None,
        music_level: float = 0.32, fade_out: float = 1.2) -> np.ndarray:
    """``sfx`` = [(start_seconds, samples, volume)]. Returns a float array in [-1, 1]."""
    n = int(total_sec * SR)
    out = _fit(voice.astype(np.float32), n).copy()
    if music is not None and len(music):
        m = _fit(music.astype(np.float32), n)
        out += m * music_level * duck_gain(out)
    for start, samples, vol in (sfx or []):
        i = int(start * SR)
        if i < 0 or i >= n:
            continue
        seg = samples[: n - i]
        out[i:i + len(seg)] += seg * vol
    fo = min(int(fade_out * SR), n)
    if fo > 0:
        out[-fo:] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    if peak > 0.97:
        out = out / peak * 0.97
    return out
