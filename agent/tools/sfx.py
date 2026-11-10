"""Tiny sound effects generated with numpy - no downloads, no copyright."""
from __future__ import annotations

import numpy as np

from agent.tools.media import SR


def whoosh(duration: float = 0.45, seed: int = 7) -> np.ndarray:
    """Filtered-noise swell: a classic scene-transition whoosh."""
    n = int(SR * duration)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n).astype(np.float32)
    win = 24
    smooth = np.convolve(noise, np.ones(win, dtype=np.float32) / win, mode="same")
    t = np.linspace(0, 1, n, dtype=np.float32)
    env = np.sin(np.pi * t) ** 2 * (0.4 + 0.6 * t)          # swell in, fade out
    sweep = np.sin(2 * np.pi * (180 + 900 * t) * t * duration) * 0.15
    out = (smooth * 3.0 + sweep) * env
    return out / (np.max(np.abs(out)) + 1e-6) * 0.8


def pop(duration: float = 0.12) -> np.ndarray:
    """Short attention 'pop'."""
    n = int(SR * duration)
    t = np.arange(n, dtype=np.float32) / SR
    tone = np.sin(2 * np.pi * 800 * t) * np.exp(-30 * t)
    return tone / (np.max(np.abs(tone)) + 1e-6) * 0.8
