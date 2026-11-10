"""The "Thinking…" animation (bot/thinking.py): Telegram message edits, native web sinks, silent no-op."""
import time

from bot import commands, dispatch
from bot.thinking import GLYPHS, Thinking, current, fmt_elapsed, status
from tests.conftest import msg


class Sink:
    """A Telegram-like sink that supports the animation (like bot.tg.Telegram)."""
    supports_thinking = True

    def __init__(self):
        self.sent, self.edits, self.deleted, self.actions = [], [], [], []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(text)
        return {"message_id": 7}

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append((message_id, text))

    def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)

    def send_chat_action(self, chat_id, action="typing"):
        self.actions.append(action)


class NativeSink:
    """A sink that animates by itself (like the web chat): it only receives events."""
    supports_thinking = True

    def __init__(self):
        self.calls = []

    def thinking_start(self, chat, title):
        self.calls.append(("start", title))

    def thinking_step(self, chat, text):
        self.calls.append(("step", text))

    def thinking_stop(self, chat, final):
        self.calls.append(("stop", final))

    def send_message(self, chat, text, reply_markup=None):
        self.calls.append(("msg", text))
        return {"message_id": 1}


def wait_for(cond, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_elapsed_format():
    assert fmt_elapsed(0) == "0s" and fmt_elapsed(7.9) == "7s" and fmt_elapsed(67) == "1:07" and fmt_elapsed(-3) == "0s"


def test_telegram_message_is_edited_with_steps_and_removed_at_the_end():
    s = Sink()
    with Thinking(s, 5, interval=0.02) as t:
        assert s.sent and s.sent[0].startswith(GLYPHS[0]) and "Thinking…" in s.sent[0]
        assert s.actions == ["typing"]                          # "typing…" shows at once
        t.step("🔎 Searching the web")
        assert wait_for(lambda: any("Searching the web" in e[1] for e in s.edits))
        assert wait_for(lambda: len({e[1] for e in s.edits}) >= 3)     # the frames really change
    assert all(mid == 7 for mid, _ in s.edits)
    assert s.deleted == [7]                                     # one message, gone when the work is done


def test_final_text_replaces_the_animation_instead_of_deleting_it():
    s = Sink()
    t = Thinking(s, 5, "Making your video", interval=0.02).start()
    t.step("🎙 Voice 1/3")
    t.stop(final="✅ Video ready!")
    assert s.edits[-1] == (7, "✅ Video ready!") and s.deleted == []
    t.stop(final="again")                                       # stopping twice is harmless
    assert s.edits[-1] == (7, "✅ Video ready!")


def test_steps_are_trimmed_and_deduplicated():
    s = Sink()
    t = Thinking(s, 5, interval=5, max_steps=3).start()
    for i in range(6):
        t.step(f"step {i}")
    t.step("step 5")
    frame = t.frame()
    t.stop()
    assert "step 5" in frame and "step 3" in frame and "step 2" not in frame and frame.count("step 5") == 1


def test_gentler_edits_on_long_jobs():
    now = [0.0]
    t = Thinking(Sink(), 1, interval=1.5, clock=lambda: now[0])
    t._t0 = 0.0
    assert t._delay() == 1.5
    now[0] = 120.0
    assert t._delay() == 3.0


def test_failures_in_decoration_never_break_the_work():
    class Broken(Sink):
        def edit_message_text(self, *a, **k):
            raise RuntimeError("telegram is down")

        def delete_message(self, *a, **k):
            raise RuntimeError("telegram is down")

    s = Broken()
    with Thinking(s, 5, interval=0.01) as t:
        t.step("work")
        time.sleep(0.1)                                         # edits fail, the loop must survive
    class CannotSend(Sink):
        def send_message(self, *a, **k):
            raise RuntimeError("no")

    t2 = Thinking(CannotSend(), 5).start()
    assert not t2.active
    t2.step("x")
    t2.stop()


def test_unsupported_sink_is_a_silent_noop(tg):                 # FakeTG has no supports_thinking
    with Thinking(tg, 1) as t:
        t.step("whatever")
        assert not t.active and current() is None
    assert tg.sent == [] and tg.edited == []


def test_status_is_a_step_inside_the_animation_and_a_message_outside(tg):
    status(tg, 1, "🔎 outside")
    assert tg.last == "🔎 outside"
    s = Sink()
    with Thinking(s, 5, interval=5):
        assert current() is not None
        status(s, 5, "🔎 inside")
        assert "🔎 inside" in current().frame()
    assert s.sent == [s.sent[0]] and "inside" not in s.sent[0] and current() is None


def test_native_sink_gets_events_only():
    n = NativeSink()
    with Thinking(n, "web:jon:1", "Making your video") as t:
        t.step("a")
        t.step("a")                                             # duplicate ignored
        t.step("b")
    assert n.calls == [("start", "Making your video"), ("step", "a"), ("step", "b"), ("stop", None)]
    n2 = NativeSink()
    t2 = Thinking(n2, 1).start()
    t2.stop(final="done")
    assert n2.calls[-1] == ("stop", "done")


# ------------------------------------------------------------------ wired into the command handlers
def test_slow_commands_run_inside_the_animation(isolated, monkeypatch):
    monkeypatch.setattr("agent.providers.llm.complete", lambda *a, **k: (time.sleep(0.05), "42")[1])
    s = Sink()
    commands.handle_update(msg("/ask what is 6x7"), s)
    assert "Thinking…" in s.sent[0] and s.sent[-1] == "42" and s.deleted == [7]
    s2 = Sink()
    commands.handle_update(msg("kya hai 6x7 bata do"), s2)         # plain text -> /ask -> also animated
    assert "Thinking…" in s2.sent[0] and s2.sent[-1] == "42"


def test_research_progress_line_becomes_a_step_not_a_message(isolated, monkeypatch):
    monkeypatch.setattr("agent.tools.research.research_topic",
                        lambda topic: {"topic": topic, "summary": "sum", "sources": []})
    s = Sink()
    commands.handle_update(msg("/research ai tools"), s)
    assert len(s.sent) == 2 and "Thinking…" in s.sent[0] and s.sent[1].endswith("sum")


def test_without_animation_support_the_old_status_message_still_appears(isolated, monkeypatch, tg):
    monkeypatch.setattr("agent.tools.research.research_topic",
                        lambda topic: {"topic": topic, "summary": "sum", "sources": []})
    commands.handle_update(msg("/research ai tools"), tg)
    assert "Searching the web" in tg.sent[0][1]


def test_video_command_only_queues_so_it_has_no_animation(isolated, monkeypatch):
    monkeypatch.setattr(dispatch, "dispatch_video", lambda p, c, prefs: (True, "queued"))
    s = Sink()
    commands.handle_update(msg("/video calm reel"), s)
    assert s.sent == ["queued"] and s.deleted == []
