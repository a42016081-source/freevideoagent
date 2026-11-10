"""ffmpeg / ffprobe helpers + tiny WAV utilities (16-bit mono 44.1 kHz everywhere)."""
from __future__ import annotations

import logging
import subprocess
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
SR = 44100


class FFmpegError(RuntimeError):
    pass


def run_ffmpeg(args: list[str], cwd: Path | None = None, timeout: int = 1800) -> None:
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args]
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(f"ffmpeg timed out after {timeout}s") from e
    if p.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({p.returncode}): {p.stderr.strip()[-800:]}")


def probe_duration(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except Exception:  # noqa: BLE001
        return 0.0


def to_wav(src: Path, dst: Path, duration: float | None = None, loop: bool = False) -> Path:
    """Convert any audio file to 16-bit mono 44.1 kHz WAV, optionally looped / trimmed."""
    args = []
    if loop:
        args += ["-stream_loop", "-1"]
    args += ["-i", str(src), "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le"]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    args.append(str(dst))
    run_ffmpeg(args)
    return dst


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit WAV supported")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
    return data


def write_wav(path: Path, samples: np.ndarray) -> Path:
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path
