"""Cloudflare D1 backend, tested against a fake of D1's REST API backed by real SQLite
(D1 *is* SQLite, so the SQL behaves the same)."""
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from agent import d1, memory
from agent import skills as sk
from agent.config import settings
from agent.skills import store
from bot import commands
from tests.conftest import msg
from tests.test_skills import GOOD_MD, GOOD_PY, make_zip

DB = {"conn": None, "calls": 0, "tokens": set(), "fail_next": 0}


class FakeD1(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        DB["calls"] += 1
        DB["tokens"].add(self.headers.get("Authorization"))
        if self.headers.get("Authorization") != "Bearer good-token":
            return self._send(403, {"success": False, "errors": [{"message": "Authentication error"}]})
        if DB["fail_next"]:
            DB["fail_next"] -= 1
            return self._send(503, {})
        assert self.path.endswith("/accounts/acct/d1/database/dbid/query"), self.path
        try:
            cur = DB["conn"].execute(body["sql"], body.get("params", []))
            rows = [dict(r) for r in cur.fetchall()]
            DB["conn"].commit()
        except sqlite3.Error as e:
            return self._send(400, {"success": False, "errors": [{"message": str(e)}]})
        self._send(200, {"success": True, "errors": [], "result": [{"success": True, "results": rows}]})

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def cloud(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    DB.update(conn=conn, calls=0, tokens=set(), fail_next=0)
    srv = HTTPServer(("127.0.0.1", 0), FakeD1)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(settings, "CLOUDFLARE_API_BASE", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(settings, "CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "D1_DATABASE_ID", "dbid")
    monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "good-token")
    d1._schema_ready.clear()
    monkeypatch.setattr("time.sleep", lambda s: None)          # no real waiting on retries
    yield conn
    srv.shutdown()


def new_machine(monkeypatch, tmp_path, name):
    """Simulate a brand-new GitHub runner: empty skills/state folders, same database."""
    monkeypatch.setattr(settings, "SKILLS_DIR", tmp_path / name / "skills")
    monkeypatch.setattr(settings, "STATE_DIR", tmp_path / name / "state")
    (tmp_path / name / "skills").mkdir(parents=True)


def test_disabled_without_credentials():
    assert not d1.enabled()
    with pytest.raises(d1.D1Error):
        d1.query("SELECT 1")


def test_schema_is_created_and_status_works(cloud):
    assert d1.status() == {"prefs": 0, "notes": 0, "jobs": 0, "skills": 0}
    names = {r[0] for r in cloud.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"prefs", "notes", "jobs", "skills", "skill_chunks"} <= names


def test_prefs_notes_jobs_persist_across_machines(cloud, monkeypatch, tmp_path):
    new_machine(monkeypatch, tmp_path, "runner1")
    memory.set_pref(111, "language", "hi")
    memory.set_pref(111, "music", False)
    memory.add_feedback(111, "shorter please")
    n1 = memory.add_note(111, "my channel is @xyz")
    memory.add_note(111, "I like punchy lines")
    memory.save_job(111, "a reel", "{}", "/x.mp4", "done", 77)

    new_machine(monkeypatch, tmp_path, "runner2")                   # restart: nothing local
    assert memory.get_prefs(111) == {"language": "hi", "music": False, "feedback": ["shorter please"]}
    assert [n["text"] for n in memory.list_notes(111)] == ["my channel is @xyz", "I like punchy lines"]
    assert memory.list_jobs(111)[0]["id"] == 77
    assert memory.get_prefs(222) == {} and memory.list_notes(222) == []     # users are isolated
    assert memory.delete_note(111, n1) and len(memory.list_notes(111)) == 1
    assert not memory.delete_note(222, n1)
    memory.set_pref(111, "music", None)                                      # None removes a key
    assert "music" not in memory.get_prefs(111)


def test_quotes_and_unicode_are_safe(cloud):
    evil = "x'); DROP TABLE prefs;-- नमस्ते 🔥"
    memory.add_note(1, evil)
    assert memory.list_notes(1)[0]["text"] == evil
    assert d1.status()["prefs"] == 0                                         # table still there


def test_token_never_leaks_in_errors(cloud, monkeypatch):
    monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "bad-secret-token")
    with pytest.raises(d1.D1Error) as e:
        d1.query("SELECT 1")
    assert "bad-secret-token" not in str(e.value) and "permission" in str(e.value)


def test_transient_errors_are_retried(cloud):
    DB["fail_next"] = 2
    assert d1.query("SELECT 1 AS x") == [{"x": 1}]


def test_skill_survives_restart_with_approval(cloud, monkeypatch, tmp_path):
    new_machine(monkeypatch, tmp_path, "runner1")
    res = sk.install_from_zip(make_zip({"hello/SKILL.md": GOOD_MD, "hello/script.py": GOOD_PY,
                                        "hello/data/big.txt": "x" * 200_000}), "hello.zip")
    assert res.ok, res.message
    assert cloud.execute("SELECT approved FROM skills").fetchone()[0] == 0
    assert cloud.execute("SELECT COUNT(*) FROM skill_chunks").fetchone()[0] >= 1
    sk.approve_skill(res.name)
    assert cloud.execute("SELECT approved FROM skills").fetchone()[0] == 1

    new_machine(monkeypatch, tmp_path, "runner2")                   # restart
    assert sk.scan_skills() == {}
    out = store.sync_down()
    assert out["downloaded"] == 1 and not out["failed"]
    s = sk.scan_skills()["hello_skill"]
    assert s.approved and (s.path / "data" / "big.txt").stat().st_size == 200_000
    assert sk.run_skill("hello_skill", {"who": "Ravi"})["text"] == "hi Ravi"      # and it actually runs
    assert store.sync_down()["downloaded"] == 0                     # unchanged -> no re-download


def test_remove_and_update_propagate(cloud, monkeypatch, tmp_path):
    new_machine(monkeypatch, tmp_path, "m1")
    assert sk.install_from_markdown(GOOD_MD, "x", "s").ok
    sk.approve_skill("hello_skill")
    new_machine(monkeypatch, tmp_path, "m2")
    store.sync_down()
    assert sk.scan_skills()["hello_skill"].approved

    new_machine(monkeypatch, tmp_path, "m1b")
    store.sync_down()
    assert sk.install_from_markdown(GOOD_MD.replace("Be nice.", "Be VERY nice."), "x", "s", force=True).ok
    assert not sk.scan_skills()["hello_skill"].approved             # a new version needs approval again
    assert cloud.execute("SELECT COUNT(DISTINCT version) FROM skill_chunks").fetchone()[0] == 1   # old chunks gone

    new_machine(monkeypatch, tmp_path, "m3")
    store.sync_down()
    assert "VERY nice" in (sk.scan_skills()["hello_skill"].path / "SKILL.md").read_text()
    assert "not found" not in sk.remove_skill("hello_skill")
    assert cloud.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 0
    assert cloud.execute("SELECT COUNT(*) FROM skill_chunks").fetchone()[0] == 0
    new_machine(monkeypatch, tmp_path, "m4")
    store.sync_down()
    assert sk.scan_skills() == {}


def test_tampered_chunk_is_rejected_not_installed(cloud, monkeypatch, tmp_path):
    new_machine(monkeypatch, tmp_path, "a")
    sk.install_from_markdown(GOOD_MD, "x", "s")
    cloud.execute("UPDATE skill_chunks SET data = 'AAAA' || substr(data, 5)")
    cloud.commit()
    new_machine(monkeypatch, tmp_path, "b")
    out = store.sync_down()
    assert out["failed"] == ["hello_skill"] and sk.scan_skills() == {}


def test_d1_is_source_of_truth_but_builtin_is_kept(cloud, monkeypatch, tmp_path):
    root = tmp_path / "c"
    (root / "skills" / "mine").mkdir(parents=True)
    (root / "skills" / "mine" / "SKILL.md").write_text(GOOD_MD)
    (root / "skills" / "core").mkdir()
    (root / "skills" / "core" / "SKILL.md").write_text(GOOD_MD)
    (root / "skills" / "core" / ".skill_meta.json").write_text('{"approved": true, "source": "builtin"}')
    monkeypatch.setattr(settings, "SKILLS_DIR", root / "skills")
    out = store.sync_down()
    assert out["removed"] == 1 and set(sk.scan_skills()) == {"core"}


def test_too_big_skill_is_refused_cleanly(cloud, monkeypatch, tmp_path):
    new_machine(monkeypatch, tmp_path, "big")
    import os
    blob = os.urandom(2_000_000)
    res = sk.install_from_zip(make_zip({"SKILL.md": GOOD_MD, "a.bin": blob, "b.bin": os.urandom(2_000_000)}), "big.zip")
    assert not res.ok and "D1" in res.message and not sk.scan_skills()


def test_migrate_local_state_and_skills(cloud, monkeypatch, tmp_path):
    new_machine(monkeypatch, tmp_path, "old")
    monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "")        # work in JSON mode first
    memory.set_pref(111, "language", "en")
    memory.add_note(111, "legacy note")
    sk.install_from_markdown(GOOD_MD, "x", "s")
    monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "good-token")   # now switch D1 on
    assert d1.migrate_local() == "migrated 1 user settings, 1 notes, 1 skills"
    new_machine(monkeypatch, tmp_path, "new")
    assert memory.get_prefs(111) == {"language": "en"} and memory.list_notes(111)[0]["text"] == "legacy note"
    store.sync_down()
    assert "hello_skill" in sk.scan_skills()


# ------------------------------------------------------------- bot commands
def test_remember_notes_forget_and_storage(cloud, tg, monkeypatch, tmp_path):
    new_machine(monkeypatch, tmp_path, "bot")
    commands.handle_update(msg("/remember my channel is @xyz"), tg)
    assert "Remembered #1" in tg.last and "D1" in tg.last
    commands.handle_update(msg("/notes"), tg)
    assert "#1 my channel is @xyz" in tg.last
    commands.handle_update(msg("/storage"), tg)
    assert "connected" in tg.last and "notes: 1" in tg.last
    commands.handle_update(msg("/forget 1"), tg)
    commands.handle_update(msg("/notes"), tg)
    assert "Nothing yet" in tg.last


def test_video_job_receives_notes(cloud, tg, monkeypatch):
    seen = {}
    from bot import dispatch
    monkeypatch.setattr(dispatch, "dispatch_video", lambda p, c, prefs: (seen.update(prefs=prefs), (True, "ok"))[1])
    memory.add_note(111, "always end with a question")
    commands.handle_update(msg("/video test reel"), tg)
    assert seen["prefs"]["notes"] == ["always end with a question"]
    assert "always end with a question" in dispatch._slim_prefs(seen["prefs"])


def test_planner_uses_notes(monkeypatch):
    from agent.planner import core
    captured = {}
    monkeypatch.setattr(core, "complete_ex", lambda system, *a, **k: (captured.update(system=system), (_ for _ in ()).throw(RuntimeError()))[1])
    core.make_plan("english calm video", {"notes": ["channel name is Zen Daily"]})
    assert "Zen Daily" in captured["system"]


def test_storage_command_without_d1(tg):
    commands.handle_update(msg("/storage"), tg)
    assert "JSON files" in tg.last


def test_commit_state_is_noop_with_d1(cloud):
    from bot import gitstate
    assert gitstate.commit_state() is True
