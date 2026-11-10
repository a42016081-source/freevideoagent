import json

from agent import memory
from agent import skills as sk
from bot import commands, dispatch
from tests.conftest import msg
from tests.test_skills import GOOD_MD, GOOD_PY, make_zip


def test_unauthorised_user_is_refused_and_told_their_id(tg):
    commands.handle_update(msg("/video secret plan", uid=999, chat=999), tg)
    assert "Not authorised" in tg.last and "999" in tg.last


def test_empty_allowlist_fails_closed(tg, monkeypatch):
    from agent.config import settings
    monkeypatch.setattr(settings, "ALLOWED_USER_IDS", [])
    commands.handle_update(msg("/help"), tg)
    assert "Not authorised" in tg.last


def test_start_and_unknown_command(tg):
    commands.handle_update(msg("/start"), tg)
    assert "/addskill" in tg.last and "/video" in tg.last
    commands.handle_update(msg("/nope"), tg)
    assert "Unknown" in tg.last
    commands.handle_update(msg("/help@MyBot"), tg)
    assert "/addskill" in tg.last


def test_video_dispatch_passes_prefs(tg, monkeypatch):
    seen = {}
    monkeypatch.setattr(dispatch, "dispatch_video",
                        lambda p, c, prefs: (seen.update(p=p, c=c, prefs=prefs), (True, "queued"))[1])
    memory.set_pref(111, "language", "en")
    commands.handle_update(msg("ek 30 second ka reel bana"), tg)       # plain text -> routed to video
    assert seen["p"] == "ek 30 second ka reel bana" and seen["prefs"]["language"] == "en" and tg.last == "queued"


def test_video_without_any_backend_says_why(tg, monkeypatch):
    monkeypatch.setenv("RENDER_MODE", "cloud")
    commands.handle_update(msg("/video test"), tg)
    assert "GITHUB_REPO" in tg.last


def test_settings_buttons_persist_prefs(tg):
    commands.handle_update(msg("/settings"), tg)
    assert tg.sent[-1][2]["inline_keyboard"]
    for data in ("pref:aspect_ratio:16:9", "pref:music:false", "pref:voice:hi-IN-MadhurNeural"):
        commands.handle_update({"callback_query": {"id": "c", "from": {"id": 111}, "data": data,
                                                   "message": {"message_id": 9, "chat": {"id": 111}}}}, tg)
    p = memory.get_prefs(111)
    assert p == {"aspect_ratio": "16:9", "music": False, "voice": "hi-IN-MadhurNeural"}


def test_callback_from_stranger_is_ignored(tg):
    commands.handle_update({"callback_query": {"id": "c", "from": {"id": 5}, "data": "pref:music:false",
                                               "message": {"message_id": 9, "chat": {"id": 5}}}}, tg)
    assert memory.get_prefs(5) == {} and tg.answers[-1][1] == "Not allowed"


def test_unknown_pref_key_is_rejected(tg):
    commands.handle_update({"callback_query": {"id": "c", "from": {"id": 111}, "data": "pref:ALLOWED_USER_IDS:5",
                                               "message": {"message_id": 9, "chat": {"id": 111}}}}, tg)
    assert memory.get_prefs(111) == {}


def test_feedback_and_watermark(tg):
    commands.handle_update(msg("/feedback shorter please"), tg)
    commands.handle_update(msg("/watermark @me"), tg)
    p = memory.get_prefs(111)
    assert p["feedback"] == ["shorter please"] and p["watermark"] == "@me"


def test_skill_upload_approval_flow_end_to_end(tg):
    tg.files["f1"] = make_zip({"SKILL.md": GOOD_MD, "script.py": GOOD_PY})
    commands.handle_update(msg("", document={"file_id": "f1", "file_name": "hello.zip"}), tg)
    text, markup = tg.sent[-1][1], tg.sent[-1][2]
    assert "NOT active" in text and "Things to check" not in text.replace("No risky", "")
    data = [b["callback_data"] for b in markup["inline_keyboard"][0]]
    assert data == ["skill:approve:hello_skill", "skill:reject:hello_skill"]
    assert not sk.scan_skills()["hello_skill"].approved

    commands.handle_update({"callback_query": {"id": "c", "from": {"id": 111}, "data": data[0],
                                               "message": {"message_id": 9, "chat": {"id": 111}}}}, tg)
    assert sk.scan_skills()["hello_skill"].approved
    commands.handle_update(msg('/runskill hello_skill {"who": "Ravi"}'), tg)
    assert tg.photos and "hi Ravi" in tg.photos[-1][2]


def test_reject_removes_skill(tg):
    sk.install_from_markdown(GOOD_MD, "x", "s")
    commands.handle_update({"callback_query": {"id": "c", "from": {"id": 111}, "data": "skill:reject:hello_skill",
                                               "message": {"message_id": 9, "chat": {"id": 111}}}}, tg)
    assert not sk.scan_skills()


def test_upload_of_wrong_file_type(tg):
    commands.handle_update(msg("", document={"file_id": "x", "file_name": "virus.exe"}), tg)
    assert ".zip" in tg.last


def test_skills_listing_and_bad_runskill_json(tg):
    commands.handle_update(msg("/skills"), tg)
    assert "No skills" in tg.last
    commands.handle_update(msg("/runskill x {bad json"), tg)
    assert "Bad JSON" in tg.last


def test_connectors_listing(tg):
    commands.handle_update(msg("/connectors"), tg)
    assert "github" in tg.last and "mcp" in tg.last and "webhook" in tg.last


def test_handler_errors_do_not_crash_the_bot(tg, monkeypatch):
    monkeypatch.setitem(commands.COMMANDS, "boom", lambda *a: 1 / 0)
    commands.handle_update(msg("/boom"), tg)
    assert "Something went wrong" in tg.last and "ZeroDivisionError" in tg.last


def test_status_without_github(tg):
    commands.handle_update(msg("/status"), tg)
    assert "GITHUB_REPO" in tg.last


def test_dispatch_prefs_are_slimmed_and_valid_json():
    s = dispatch._slim_prefs({"voice": "v", "junk": "x" * 10, "feedback": ["a"] * 5})
    d = json.loads(s)
    assert "junk" not in d and d["voice"] == "v"
    big = json.loads(dispatch._slim_prefs({"feedback": ["y" * 200] * 30, "voice": "v"}))
    assert "feedback" not in big
