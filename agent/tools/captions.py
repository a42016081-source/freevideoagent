"""Karaoke captions as an ASS subtitle file, burned in by ffmpeg/libass.

Why ASS instead of drawing text with Pillow/ImageMagick: libass uses HarfBuzz, so Devanagari
(Hindi) conjuncts and matras are shaped correctly, and the word-by-word highlight is a native
ASS karaoke effect - no per-word clips, no ImageMagick.
"""
from __future__ import annotations

import logging
from pathlib import Path

from agent.config import cfg
from agent.util import has_devanagari

log = logging.getLogger(__name__)

Word = tuple[str, float, float]


def ass_time(t: float) -> str:
    t = max(t, 0.0)
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _clean(word: str) -> str:
    return word.replace("{", "(").replace("}", ")").replace("\\", "").replace("\n", " ").strip()


def _bgr(hex_rgb: str) -> str:
    hex_rgb = hex_rgb.lstrip("#")
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    return f"&H00{b}{g}{r}".upper()


def font_for(text: str) -> str:
    if has_devanagari(text):
        return cfg("captions.font_devanagari", "Noto Sans Devanagari")
    return cfg("captions.font_latin", "DejaVu Sans")


def chunk_words(words: list[Word], max_words: int = 3) -> list[list[Word]]:
    """Group words into short on-screen lines (also break after sentence punctuation)."""
    lines, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or w[0].rstrip().endswith((".", "!", "?", "।", ",")):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return lines


def build_ass(words: list[Word], w: int, h: int, out_path: Path, *,
              watermark: str | None = None, total: float | None = None) -> Path:
    """Write an ASS file. ``words`` = [(word, start_sec, end_sec)] in absolute video time."""
    sample = " ".join(x[0] for x in words)
    font = font_for(sample)
    fs = int(min(w, h) * 0.068)
    margin_v = int(h * (0.20 if h > w else 0.10))
    highlight = _bgr(cfg("captions.highlight", "FFE500"))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{fs},{highlight},&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{max(4, fs // 14)},2,2,{int(w * 0.06)},{int(w * 0.06)},{margin_v},1
Style: Wm,DejaVu Sans,{max(22, fs // 2)},&H99FFFFFF,&H99FFFFFF,&H99000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,9,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    max_words = int(cfg("captions.max_words_per_line", 3))
    for line in chunk_words(words, max_words):
        start, end = line[0][1], line[-1][2] + 0.25
        parts = []
        for i, (txt, ws, we) in enumerate(line):
            nxt = line[i + 1][1] if i + 1 < len(line) else we
            cs = max(int(round((max(nxt, we) - ws) * 100)), 1)
            parts.append(f"{{\\k{cs}}}{_clean(txt)}")
        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{' '.join(parts)}")
    if watermark:
        end = ass_time(total if total else (words[-1][2] + 1 if words else 5))
        events.append(f"Dialogue: 1,{ass_time(0)},{end},Wm,,0,0,0,,{_clean(watermark)}")
    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return out_path


def whisper_words(audio_path: Path, language: str | None = None) -> list[Word] | None:
    """OPTIONAL: real word timestamps with faster-whisper (CPU). Returns None if unavailable.
    Install with `pip install faster-whisper` and set CAPTIONS_ALIGN=whisper."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    try:
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(audio_path), language=language, word_timestamps=True)
        return [(w.word.strip(), w.start, w.end) for s in segments for w in (s.words or []) if w.word.strip()]
    except Exception as e:  # noqa: BLE001
        log.warning("whisper alignment failed: %s", e)
        return None
