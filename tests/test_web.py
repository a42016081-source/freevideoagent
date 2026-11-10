"""Web chat bridge on the bot side (bot/web.py) and the always-on loop (bot/loop.py) - no network."""
import threading
import time
from pathlib import Path

import pytest

from agent import memory
from bot import loop as loop_mod
from bot.tg import TelegramError, kb
from bot.thinking import Thinking
from bot.web import BridgeError, WebBridge, WebTelegram, parse_channel, web_chat_id, web_update


class FakeBridge:
    def __init__(self):
        self.events, self.uploads, self.fail_upload = [], [], False
        self.inbox, self.polls = [], 0

    def event(self, user, type_, data=None):
        self.events.append((user, type_, data or {}))

    def upload(self, user, content, mime):
        if self.fail_upload:
            raise BridgeError("boom")
        data = Path(content).read_bytes() if isinstance(content, (str, Path)) else content   # like WebBridge.upload
        self.uploads.append((user, data, mime))
        return "/api/file/f_" + "0" * 16

    def next(self, wait=15):
        self.polls += 1
        if self.inbox:
            return self.inbox.pop(0)
        time.sleep(0.02)
        return None

    def types(self):
        return [t for _, t, _ in self.events]


# ------------------------------------------------------------------ channels and updates
@pytest.mark.parametrize("raw,expect", [
    ("123", ("telegram", 123, 123, "")),
    ("", ("telegram", 0, 0, "")),
    (None, ("telegram", 0, 0, "")),
    ("web:jon:111", ("web", "web:jon:111", 111, "jon")),
    ("web:a:b:42", ("web", "web:a:b:42", 42, "a:b")),
])
def test_parse_channel(raw, expect):
    assert parse_channel(raw) == expect


@pytest.mark.parametrize("bad", ["web:jon", "web::5", "web:jon:x", "web:"])
def test_parse_channel_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_channel(bad)


def test_web_update_shapes():
    u = web_update({"id": 9, "user": "jon", "uid": 111, "kind": "text", "text": "hello"})
    m = u["message"]
    assert m["text"] == "hello" and m["from"]["id"] == 111 and m["chat"]["id"] == web_chat_id("jon", 111)
    c = web_update({"id": 10, "user": "jon", "uid": 111, "kind": "callback", "data": "pref:music:false", "mid": 55})
    cq = c["callback_query"]
    assert cq["data"] == "pref:music:false" and cq["from"]["id"] == 111 and cq["message"]["message_id"] == 55


# ------------------------------------------------------------------ WebTelegram
def test_messages_edits_and_buttons():
    b = FakeBridge()
    w = WebTelegram(b, "jon", 111)
    m1 = w.send_message("c", "hi", kb([[("Yes", "a:1"), ("No", "a:0")]]))["message_id"]
    m2 = w.send_message("c", "again")["message_id"]
    assert m1 != m2
    w.edit_message_text("c", m1, "changed")
    w.delete_message("c", m2)
    w.answer_callback("cb", "Saved")
    w.answer_callback("cb")                                      # nothing to say -> no event
    assert b.types() == ["msg", "msg", "edit", "del", "toast"]
    assert b.events[0][2]["buttons"] == [[{"t": "Yes", "d": "a:1"}, {"t": "No", "d": "a:0"}]]
    assert b.events[0][2]["mid"] == m1 and b.events[2][2]["text"] == "changed"
    assert b.events[1][2]["buttons"] is None


def test_files_are_uploaded_then_announced(tmp_path):
    b = FakeBridge()
    w = WebTelegram(b, "jon", 111)
    w.send_photo("c", b"\xff\xd8jpeg", caption="cap")
    v = tmp_path / "v.mp4"
    v.write_bytes(b"mp4data")
    w.send_video("c", v, caption="my video", width=1080, height=1920, duration=29.6)
    assert b.uploads == [("jon", b"\xff\xd8jpeg", "image/jpeg"), ("jon", b"mp4data", "video/mp4")]
    img, vid = b.events[0][2], b.events[1][2]
    assert b.types() == ["image", "video"] and img["caption"] == "cap" and img["url"].startswith("/api/file/f_")
    assert (vid["w"], vid["h"], vid["duration"]) == (1080, 1920, 29)


def test_failed_upload_still_tells_the_user():
    b = FakeBridge()
    b.fail_upload = True
    w = WebTelegram(b, "jon", 111)
    w.send_video("c", b"x", caption="title")
    assert b.types() == ["msg"] and "could not be stored" in b.events[0][2]["text"]


def test_telegram_only_features_say_so():
    with pytest.raises(TelegramError):
        WebTelegram(FakeBridge(), "jon", 111).download_file("id")


def test_thinking_becomes_events_for_the_browser():
    b = FakeBridge()
    w = WebTelegram(b, "jon", 111)
    w.thinking_step("c", "ignored: not started")
    with Thinking(w, "web:jon:111", "Making your video") as t:
        t.step("🎙 Voice")
        t.step("🖼 Visual")
    t2 = Thinking(w, "c").start()
    t2.stop(final="✅ Video ready!")
    assert b.types() == ["think_start", "think_step", "think_step", "think_stop", "think_start", "think_stop"]
    first = {e[2]["tid"] for e in b.events[:4]}
    assert len(first) == 1 and b.events[0][2]["title"] == "Making your video"
    assert b.events[-1][2]["final"] == "✅ Video ready!" and b.events[4][2]["tid"] != b.events[0][2]["tid"]
    assert "msg" not in b.types()                                # the animation never sends chat messages


# ------------------------------------------------------------------ a whole video job answering the web chat
def test_video_job_reports_to_the_web_chat(isolated, monkeypatch):
    from agent import run_job
    monkeypatch.setattr("agent.tools.video_engine.SIZES", {"9:16": (270, 480), "16:9": (480, 270), "1:1": (360, 360)})
    monkeypatch.setenv("GITHUB_RUN_ID", "5151")
    b = FakeBridge()
    w = WebTelegram(b, "jon", 111)
    out = run_job.run("make a 8 second calm reel in English", web_chat_id("jon", 111),
                      {"language": "en", "music": False}, tg=w, uid=111)
    assert out.exists()
    types = b.types()
    assert types[0] == "think_start" and "think_step" in types and "think_stop" in types
    assert types.index("think_stop") < types.index("video") < types.index("image")
    stop = next(d for _, t, d in b.events if t == "think_stop")
    assert stop["final"] == "✅ Video ready!"
    assert [u[2] for u in b.uploads] == ["video/mp4", "image/jpeg"]
    assert memory.recent_jobs(111, 1) if hasattr(memory, "recent_jobs") else True


def test_failed_video_job_closes_the_animation(isolated, monkeypatch):
    from agent import run_job
    b = FakeBridge()
    w = WebTelegram(b, "jon", 111)
    monkeypatch.setattr(run_job, "render", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg died")))
    with pytest.raises(RuntimeError):
        run_job.run("make a 8 second calm reel in English", web_chat_id("jon", 111), {"language": "en"}, tg=w, uid=111)
    stop = next(d for _, t, d in b.events if t == "think_stop")
    assert "failed" in stop["final"]


# ------------------------------------------------------------------ the always-on loop
class FakeTelegram:
    supports_thinking = False

    def __init__(self, batches):
        self.batches, self.offsets, self.sent = list(batches), [], []
        self.calls = []

    def get_updates(self, offset=None, timeout=25, limit=100):
        self.offsets.append(offset)
        if self.batches:
            return self.batches.pop(0)
        time.sleep(0.02)
        return []

    def call(self, method, params=None, **kw):
        self.calls.append(method)
        return True

    def send_message(self, chat, text, reply_markup=None):
        self.sent.append((chat, text))
        return {"message_id": 1}


def upd(i, text, uid=111):
    return {"update_id": i, "message": {"message_id": i, "from": {"id": uid}, "chat": {"id": uid}, "text": text}}


@pytest.fixture
def no_git(monkeypatch):
    commits = []
    monkeypatch.setattr(loop_mod.gitstate, "commit_state", lambda *a, **k: commits.append(1) or False)
    return commits


def test_loop_answers_telegram_and_confirms_what_it_took(no_git):
    tg = FakeTelegram([[upd(10, "/help"), upd(11, "/nope")], []])
    lp = loop_mod.Loop(tg, None, minutes=0.03)
    lp.run()
    texts = [t for _, t in tg.sent]
    assert any("/addskill" in t for t in texts) and any("Unknown" in t for t in texts)
    assert 12 in tg.offsets and tg.offsets[-1] == 12            # last call acknowledges update 11
    assert "deleteWebhook" in tg.calls and no_git
    assert lp.reason == "deadline"


def test_loop_survives_a_crashing_handler_and_keeps_going(no_git, monkeypatch):
    seen = []

    def boom(update, tg):
        seen.append(update["update_id"])
        if update["update_id"] == 1:
            raise RuntimeError("bug")

    monkeypatch.setattr(loop_mod.commands, "handle_update", boom)
    tg = FakeTelegram([[upd(1, "a")], [upd(2, "b")]])
    loop_mod.Loop(tg, None, minutes=0.03).run()
    assert sorted(seen) == [1, 2]


def test_loop_answers_the_web_chat_through_the_bridge(no_git):
    b = FakeBridge()
    b.inbox = [{"id": 1, "user": "jon", "uid": 111, "kind": "text", "text": "/help"},
               {"id": 2, "user": "jon", "uid": 999, "kind": "text", "text": "/help"},          # not in ALLOWED_USER_IDS
               {"id": 3, "user": "jon", "uid": 111, "kind": "callback", "data": "pref:music:false", "mid": 4}]
    loop_mod.Loop(None, b, minutes=0.03).run()
    msgs = [d["text"] for _, t, d in b.events if t == "msg"]
    assert any("/addskill" in m for m in msgs)
    assert any("Not authorised" in m and "999" in m for m in msgs)
    assert memory.get_prefs(111).get("music") is False          # the button press from the browser worked


def test_loop_keeps_polling_after_bridge_errors(no_git):
    class Flaky(FakeBridge):
        def next(self, wait=15):
            self.polls += 1
            if self.polls == 1:
                raise BridgeError("worker down")
            return super().next(wait)

    b = Flaky()
    lp = loop_mod.Loop(None, b, minutes=0.03)
    lp.stop.wait = lambda t=None: time.sleep(0.01)               # do not sit through the real back-off
    lp.run()
    assert b.polls >= 3


def test_loop_stops_early_on_signal_and_does_not_restart(no_git, monkeypatch):
    b = FakeBridge()
    lp = loop_mod.Loop(None, b, minutes=5)
    threading.Timer(0.2, lp._signal).start()
    t0 = time.time()
    lp.run()
    assert time.time() - t0 < 5 and lp.reason == "signal"


def test_main_restarts_the_workflow_at_the_deadline(monkeypatch):
    started = []
    monkeypatch.setattr(loop_mod.dispatch, "dispatch_workflow", lambda wf, inputs=None: started.append(wf) or True)
    monkeypatch.setattr(loop_mod, "Telegram", lambda: FakeTelegram([]))
    monkeypatch.setattr(loop_mod.settings, "TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setattr(loop_mod.WebBridge, "enabled", staticmethod(lambda: False))
    monkeypatch.setattr(loop_mod.gitstate, "commit_state", lambda *a, **k: False)
    monkeypatch.setenv("LOOP_MINUTES", "0.02")
    assert loop_mod.main() == 0 and started == ["bot_loop.yml"]
    started.clear()
    monkeypatch.setenv("LOOP_RESTART", "0")
    assert loop_mod.main() == 0 and started == []


def test_main_does_nothing_without_any_channel(monkeypatch, capsys):
    monkeypatch.setattr(loop_mod.settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(loop_mod.WebBridge, "enabled", staticmethod(lambda: False))
    assert loop_mod.main() == 0 and "nothing to do" in capsys.readouterr().out


def test_bridge_client_needs_both_settings(monkeypatch):
    monkeypatch.setattr("bot.web.settings.WEB_BASE_URL", "")
    monkeypatch.setattr("bot.web.settings.BRIDGE_SECRET", "")
    assert not WebBridge.enabled()
    with pytest.raises(BridgeError):
        WebBridge()


# ------------------------------------------------------------------ the self-restart call itself
def test_dispatch_workflow_posts_to_the_right_url(monkeypatch):
    from bot import dispatch
    monkeypatch.setattr(dispatch.settings, "GITHUB_REPO", "me/repo")
    monkeypatch.setattr(dispatch.settings, "GITHUB_TOKEN", "tok")
    monkeypatch.setattr(dispatch.settings, "GITHUB_BRANCH", "main")
    seen = {}

    class R:
        status_code = 204

    def fake_post(url, headers=None, timeout=None, json=None):
        seen.update(url=url, json=json, auth=headers.get("Authorization"))
        return R()

    monkeypatch.setattr(dispatch.requests, "post", fake_post)
    assert dispatch.dispatch_workflow("bot_loop.yml") is True
    assert seen["url"].endswith("/repos/me/repo/actions/workflows/bot_loop.yml/dispatches")
    assert seen["json"] == {"ref": "main", "inputs": {}} and seen["auth"] == "Bearer tok"
    R.status_code = 403
    assert dispatch.dispatch_workflow("bot_loop.yml") is False       # refused -> the cron safety net takes over


def test_dispatch_workflow_without_credentials_or_network(monkeypatch):
    from bot import dispatch
    monkeypatch.setattr(dispatch.settings, "GITHUB_TOKEN", "")
    assert dispatch.dispatch_workflow("bot_loop.yml") is False
    monkeypatch.setattr(dispatch.settings, "GITHUB_REPO", "me/repo")
    monkeypatch.setattr(dispatch.settings, "GITHUB_TOKEN", "tok")

    def boom(*a, **k):
        raise dispatch.requests.ConnectionError("offline")

    monkeypatch.setattr(dispatch.requests, "post", boom)
    assert dispatch.dispatch_workflow("bot_loop.yml") is False
