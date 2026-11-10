"""Background music.

1. If you put your own royalty-free tracks in assets/music/<mood>/ (mp3/wav/m4a/ogg) they are used.
   (Only add music you have the rights to - CC0 / licensed.)
2. Otherwise a short original loop is SYNTHESISED with numpy for the mood (pads + bass + simple
   drums). It is copyright-free, needs no download and has a known tempo, so scene cuts can be
   snapped to the beat.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agent.config import settings
from agent.tools.media import SR, to_wav, write_wav

MOOD_DIRS = {
    "motivational": ["motivational", "epic", "uplifting"],
    "sad": ["sad", "emotional", "ambient"],
    "energetic": ["energetic", "edm", "hiphop"],
    "calm": ["calm", "lofi", "chill"],
    "dramatic": ["dramatic", "cinematic", "tension"],
}
AUDIO_EXT = ("*.mp3", "*.wav", "*.m4a", "*.ogg", "*.flac")

# name: (bpm, [(root_midi, chord_kind) x 4 bars], drum pattern, arpeggio?)
CHORDS = {"maj": (0, 4, 7), "min": (0, 3, 7), "maj7": (0, 4, 7, 11), "min7": (0, 3, 7, 10),
          "dom7": (0, 4, 7, 10)}
STYLES = {
    "motivational": dict(bpm=100, prog=[(48, "maj"), (43, "maj"), (45, "min"), (41, "maj")],
                         kick=[0, 1, 2, 3], hat=[0.5, 1.5, 2.5, 3.5], snare=[], arp=True, swing=0.0),
    "sad": dict(bpm=70, prog=[(45, "min"), (41, "maj"), (48, "maj"), (43, "maj")],
                kick=[], hat=[], snare=[], arp=True, swing=0.0),
    "energetic": dict(bpm=126, prog=[(45, "min"), (41, "maj"), (48, "maj"), (43, "maj")],
                      kick=[0, 1, 2, 3], hat=[0.5, 1.5, 2.5, 3.5], snare=[1, 3], arp=True, swing=0.0),
    "calm": dict(bpm=78, prog=[(48, "maj7"), (45, "min7"), (50, "min7"), (43, "dom7")],
                 kick=[0, 2.5], hat=[0.5, 1.5, 2.5, 3.5], snare=[1, 3], arp=True, swing=0.12),
    "dramatic": dict(bpm=92, prog=[(50, "min"), (46, "maj"), (43, "min"), (45, "maj")],
                     kick=[0, 2], hat=[], snare=[3], arp=False, swing=0.0),
}


@dataclass
class MusicChoice:
    kind: str                 # "file" | "synth"
    mood: str
    bpm: float | None = None  # known only for synthesised music
    path: Path | None = None


def bpm_for(mood: str) -> float:
    return float(STYLES.get(mood, STYLES["motivational"])["bpm"])


def _user_tracks(mood: str) -> list[Path]:
    base = settings.ASSETS_DIR / "music"
    found: list[Path] = []
    for sub in MOOD_DIRS.get(mood, [mood]):
        d = base / sub
        if d.is_dir():
            for pat in AUDIO_EXT:
                found += list(d.glob(pat))
    return found


def resolve(mood: str) -> MusicChoice:
    tracks = _user_tracks(mood)
    if tracks:
        return MusicChoice("file", mood, None, random.choice(tracks))
    return MusicChoice("synth", mood, bpm_for(mood), None)


# ------------------------------------------------------------------ synth
def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _tri(freq: float, n: int, phase: float = 0.0) -> np.ndarray:
    t = np.arange(n, dtype=np.float32) / SR
    return (2 / np.pi * np.arcsin(np.sin(2 * np.pi * freq * t + phase))).astype(np.float32)


def _adsr(n: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(n, dtype=np.float32)
    a, r = min(int(attack * SR), n), min(int(release * SR), n)
    if a:
        env[:a] = np.linspace(0, 1, a, dtype=np.float32)
    if r:
        env[-r:] *= np.linspace(1, 0, r, dtype=np.float32)
    return env


def _soften(x: np.ndarray, win: int = 10) -> np.ndarray:
    return np.convolve(x, np.ones(win, dtype=np.float32) / win, mode="same")


def _kick(rng: np.random.Generator) -> np.ndarray:
    n = int(0.22 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    freq = 45 + 90 * np.exp(-t * 28)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return (np.sin(phase) * np.exp(-t * 14)).astype(np.float32)


def _hat(rng: np.random.Generator) -> np.ndarray:
    n = int(0.05 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    noise = np.diff(rng.standard_normal(n + 1)).astype(np.float32)   # crude high-pass
    return noise * np.exp(-t * 90) * 0.5


def _snare(rng: np.random.Generator) -> np.ndarray:
    n = int(0.14 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    noise = _soften(rng.standard_normal(n).astype(np.float32), 3)
    body = np.sin(2 * np.pi * 190 * t)
    return ((noise * 0.8 + body * 0.4) * np.exp(-t * 26)).astype(np.float32)


def _put(buf: np.ndarray, sample: np.ndarray, at_sec: float, gain: float) -> None:
    i = int(at_sec * SR)
    if i >= len(buf):
        return
    seg = sample[: len(buf) - i]
    buf[i:i + len(seg)] += seg * gain


def synthesize(mood: str, duration: float, seed: int = 11) -> np.ndarray:
    """Return ``duration`` seconds of original mood music as float32 mono."""
    st = STYLES.get(mood, STYLES["motivational"])
    rng = np.random.default_rng(seed)
    beat = 60.0 / st["bpm"]
    bar = beat * 4
    n_total = int(duration * SR) + SR
    buf = np.zeros(n_total, dtype=np.float32)
    kick, hat, snare = _kick(rng), _hat(rng), _snare(rng)

    bars = int(np.ceil(n_total / SR / bar)) + 1
    for b in range(bars):
        t0 = b * bar
        root, kind = st["prog"][b % 4]
        notes = [root + iv for iv in CHORDS[kind]]
        n_bar = int(bar * SR)
        env = _adsr(n_bar, 0.25, 0.5)
        pad = np.zeros(n_bar, dtype=np.float32)
        for midi in notes:
            f = _hz(midi + 12)
            pad += _tri(f, n_bar) + 0.6 * _tri(f * 1.004, n_bar, 0.7)
        pad = _soften(pad / len(notes)) * env * 0.30
        _put(buf, pad, t0, 1.0)
        # bass: root note on beats 1 and 3
        for bt in (0, 2):
            n_b = int(beat * 2 * SR)
            bass = np.sin(2 * np.pi * _hz(root - 12) * np.arange(n_b) / SR).astype(np.float32)
            _put(buf, bass * _adsr(n_b, 0.01, 0.25) * 0.35, t0 + bt * beat, 1.0)
        # arpeggio plucks (8th notes)
        if st["arp"]:
            for k in range(8):
                midi = notes[k % len(notes)] + 24
                n_p = int(0.35 * SR)
                tt = np.arange(n_p, dtype=np.float32) / SR
                pluck = (np.sin(2 * np.pi * _hz(midi) * tt) + 0.3 * np.sin(4 * np.pi * _hz(midi) * tt)) \
                    * np.exp(-tt * 9)
                swing = st["swing"] * beat if k % 2 else 0.0
                _put(buf, pluck.astype(np.float32), t0 + k * beat / 2 + swing, 0.10)
        # drums
        for bt in st["kick"]:
            _put(buf, kick, t0 + bt * beat, 0.55)
        for bt in st["snare"]:
            _put(buf, snare, t0 + bt * beat, 0.30)
        for bt in st["hat"]:
            _put(buf, hat, t0 + bt * beat + (st["swing"] * beat), 0.25)

    out = buf[: int(duration * SR)]
    peak = float(np.max(np.abs(out))) or 1.0
    out = out / peak * 0.7
    fi, fo = int(0.5 * SR), min(int(1.5 * SR), len(out))
    out[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)
    out[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)
    return out


def prepare(choice: MusicChoice, duration: float, out_wav: Path) -> Path:
    """Render the chosen music to ``out_wav`` (mono 44.1 kHz WAV, exactly ``duration`` seconds)."""
    if choice.kind == "file" and choice.path:
        return to_wav(choice.path, out_wav, duration=duration, loop=True)
    return write_wav(out_wav, synthesize(choice.mood, duration))
