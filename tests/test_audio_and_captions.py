import numpy as np

from agent.tools import audio_mix, captions, music, sfx, tts
from agent.tools.media import SR


# ---------------------------------------------------------------- captions
def test_ass_time_format():
    assert captions.ass_time(0) == "0:00:00.00"
    assert captions.ass_time(65.25) == "0:01:05.25"
    assert captions.ass_time(3725.5) == "1:02:05.50"


def test_chunking_breaks_on_count_and_punctuation():
    words = [(w, i, i + 1) for i, w in enumerate("a b c. d e f g".split())]
    lines = captions.chunk_words(words, 3)
    assert [[w[0] for w in ln] for ln in lines] == [["a", "b", "c."], ["d", "e", "f"], ["g"]]


def test_build_ass_escapes_override_braces_and_has_karaoke(tmp_path):
    words = [("hello", 0.0, 0.4), ("{\\bad}", 0.4, 0.8), ("नमस्ते", 0.8, 1.4)]
    out = captions.build_ass(words, 1080, 1920, tmp_path / "c.ass", watermark="@me", total=3)
    text = out.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in text and "\\k" in text and "@me" in text
    assert "{\\bad}" not in text.split("[Events]")[1].replace("{\\k", "")
    assert text.count("Dialogue:") == 2                       # 1 caption line (3 words) + watermark


def test_devanagari_gets_devanagari_font():
    assert "Devanagari" in captions.font_for("नमस्ते दुनिया")
    assert "Devanagari" not in captions.font_for("hello")


# --------------------------------------------------------------------- tts
def test_word_timings_cover_duration_in_order():
    t = tts.word_timings("one two three four", 4.0)
    assert [w[0] for w in t] == ["one", "two", "three", "four"]
    assert t[0][1] >= 0 and t[-1][2] <= 4.0
    assert all(a[2] <= b[1] + 1e-6 for a, b in zip(t, t[1:]))


def test_clean_for_tts_removes_noise():
    assert tts.clean_for_tts("Hi 🔥 #tag https://x.com/y **bold**") == "Hi tag bold"


def test_offline_tts_never_fails(tmp_path):
    sp = tts.synthesize("Hello world", "en-US-AriaNeural", tmp_path / "v.wav", offline=True)
    assert sp.path.exists() and sp.duration > 0 and sp.provider in ("silence", "espeak")


# ------------------------------------------------------------------- audio
def test_music_has_requested_length_and_known_bpm():
    for mood in ("motivational", "sad", "energetic", "calm", "dramatic"):
        x = music.synthesize(mood, 3.0)
        assert len(x) == 3 * SR and np.isfinite(x).all() and np.max(np.abs(x)) <= 1.0
    assert music.bpm_for("energetic") > music.bpm_for("sad")


def test_ducking_lowers_music_while_voice_speaks():
    voice = np.zeros(4 * SR, dtype=np.float32)
    t = np.arange(2 * SR) / SR
    voice[SR:3 * SR] = 0.3 * np.sin(2 * np.pi * 220 * t)       # speech from 1s to 3s
    g = audio_mix.duck_gain(voice)
    assert g[int(0.3 * SR)] > 0.95 and g[int(2.0 * SR)] < 0.4


def test_mix_length_and_no_clipping():
    voice = (0.9 * np.sin(np.linspace(0, 400, 2 * SR))).astype(np.float32)
    out = audio_mix.mix(voice, 2.0, music.synthesize("calm", 2.0), [(0.5, sfx.whoosh(), 0.5)])
    assert len(out) == 2 * SR and np.max(np.abs(out)) <= 0.97 + 1e-6
