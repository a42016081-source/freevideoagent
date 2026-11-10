"""End to end: the REAL web/worker.js (run by Node with a fake D1/KV) + the Python bridge + the bot loop.

Skipped when Node 22+ (needs node:sqlite) is not installed.
"""
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import requests

from bot import loop as loop_mod
from bot.web import BridgeError, WebBridge

ROOT = Path(__file__).resolve().parents[1]
H = {"X-Requested-With": "fva"}
SECRET = "bridge-secret-for-tests"


def _node_ok() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    return subprocess.run([node, "-e", "require('node:sqlite')"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _node_ok(), reason="needs Node 22+ (node:sqlite)")


@pytest.fixture(scope="module")
def base():
    mp = pytest.MonkeyPatch()
    mp.setenv("NO_PROXY", "127.0.0.1,localhost")
    mp.setenv("no_proxy", "127.0.0.1,localhost")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {**os.environ, "SESSION_SECRET": "s" * 40, "BRIDGE_SECRET": SECRET,
           "WEB_USER": "Jon", "WEB_PASSWORD": "pass 123", "WEB_UID": "111"}
    p = subprocess.Popen(["node", str(ROOT / "web" / "test" / "harness.mjs"), str(port)], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    url = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            if requests.get(url + "/healthz", timeout=0.5).text == "ok":
                break
        except requests.RequestException:
            time.sleep(0.1)
    else:
        p.kill()
        pytest.fail("worker harness did not start: " + p.stderr.read()[-500:])
    yield url
    p.terminate()
    p.wait(timeout=10)
    mp.undo()


@pytest.fixture
def bridge(base):
    return WebBridge(base, SECRET)


def login(base, ip, name="jon", password="pass 123"):
    s = requests.Session()
    r = s.post(base + "/api/login", json={"id": name, "password": password}, headers={**H, "CF-Connecting-IP": ip})
    return s, r


@pytest.fixture
def me(base):
    s, r = login(base, "10.1.1.1")
    assert r.status_code == 200, r.text
    s.headers.update(H)
    return s


def drain(bridge):
    while bridge.next(wait=0):
        pass


# ------------------------------------------------------------------ page and login
def test_page_is_served_with_security_headers(base):
    r = requests.get(base + "/")
    assert r.status_code == 200 and "FreeVideoAgent" in r.text and "think-title" in r.text
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"] and r.headers["X-Frame-Options"] == "DENY"
    assert requests.get(base + "/nothing-here").status_code == 404


def test_login_and_session(base):
    s, r = login(base, "10.2.2.2", name="  JON ")                # id is case-insensitive
    assert r.status_code == 200 and r.json()["user"] == "jon"
    assert "HttpOnly" in r.headers["Set-Cookie"] and "SameSite=Strict" in r.headers["Set-Cookie"]
    assert s.get(base + "/api/me", headers=H).json() == {"user": "jon"}
    assert s.post(base + "/api/logout", headers=H).status_code == 200
    s.cookies.clear()
    assert s.get(base + "/api/me", headers=H).status_code == 401


def test_wrong_credentials_and_unauthenticated_access(base):
    assert login(base, "10.3.3.3", password="nope")[1].status_code == 401
    assert login(base, "10.3.3.3", name="nobody")[1].status_code == 401
    for path in ("/api/events", "/api/history", "/api/me"):
        assert requests.get(base + path, headers=H).status_code == 401
    assert requests.post(base + "/api/send", json={"text": "hi"}, headers=H).status_code == 401


def test_csrf_protection(base, me):
    assert requests.post(base + "/api/login", json={"id": "jon", "password": "pass 123"}).status_code == 400
    r = me.post(base + "/api/send", json={"text": "x"}, headers={**H, "Origin": "https://evil.example"})
    assert r.status_code == 403
    forged = requests.Session()
    forged.cookies.update(me.cookies)
    assert forged.post(base + "/api/send", json={"text": "x"}).status_code == 400    # no custom header


def test_login_is_rate_limited(base):
    for _ in range(8):
        assert login(base, "10.4.4.4", name="mallory", password="guess")[1].status_code == 401
    assert login(base, "10.4.4.4", name="mallory", password="guess")[1].status_code == 429
    assert login(base, "10.4.4.4")[1].status_code == 429         # the right password does not help while locked
    assert login(base, "10.4.4.5")[1].status_code == 200         # other people are not affected


def test_session_cookie_cannot_be_forged(base):
    s = requests.Session()
    s.cookies.set("fva", "eyJ1Ijoiam9uIiwiZXhwIjo5OTk5OTk5OTk5fQ.AAAA", domain="127.0.0.1")
    assert s.get(base + "/api/me", headers=H).status_code == 401


# ------------------------------------------------------------------ chat <-> bot
def test_message_roundtrip_between_browser_and_bot(base, me, bridge):
    drain(bridge)
    assert me.post(base + "/api/send", json={"text": "  30 second calm reel  "}).json() == {"ok": True}
    item = bridge.next(wait=0)
    assert item["user"] == "jon" and item["uid"] == 111 and item["kind"] == "text" and item["text"] == "30 second calm reel"
    assert bridge.next(wait=0) is None                           # each message is handed out exactly once

    bridge.event("jon", "think_start", {"tid": "t1", "title": "Thinking"})
    bridge.event("jon", "msg", {"mid": 5, "text": "hello", "buttons": [[{"t": "Yes", "d": "a"}]]})
    ev = me.get(base + "/api/events?after=0").json()
    mine = [e for e in ev["events"] if e["type"] in ("user", "think_start", "msg")][-3:]
    assert [e["type"] for e in mine] == ["user", "think_start", "msg"]
    assert mine[0]["data"]["text"] == "30 second calm reel" and mine[2]["data"]["buttons"][0][0]["d"] == "a"
    assert me.get(base + f"/api/events?after={ev['last']}").json()["events"] == []
    hist = me.get(base + "/api/history").json()["events"]
    assert [e["seq"] for e in hist] == sorted(e["seq"] for e in hist) and hist[-1]["seq"] == ev["last"]


def test_button_press_reaches_the_bot(base, me, bridge):
    drain(bridge)
    assert me.post(base + "/api/send", json={"callback": {"data": "skill:approve:demo", "mid": 77}}).status_code == 200
    item = bridge.next(wait=0)
    assert (item["kind"], item["data"], item["mid"], item["uid"]) == ("callback", "skill:approve:demo", 77, 111)


def test_bad_messages_are_refused(base, me):
    assert me.post(base + "/api/send", json={"text": "   "}).status_code == 400
    assert me.post(base + "/api/send", data="not json", headers={"Content-Type": "application/json"}).status_code == 400


def test_flooding_is_limited(base, me, bridge):
    drain(bridge)
    codes = [me.post(base + "/api/send", json={"text": f"m{i}"}).status_code for i in range(25)]
    assert 429 in codes and codes[0] == 200
    drain(bridge)


def test_long_poll_waits_and_wakes_up(base, me, bridge):
    drain(bridge)
    t0 = time.time()
    assert bridge.next(wait=2) is None and time.time() - t0 >= 1.0     # nothing came: 204 after the wait
    threading.Timer(0.5, lambda: me.post(base + "/api/send", json={"text": "late message"})).start()
    t0 = time.time()
    item = bridge.next(wait=8)
    assert item["text"] == "late message" and time.time() - t0 < 4.0


def test_clear_history(base, me, bridge):
    bridge.event("jon", "msg", {"mid": 1, "text": "x"})
    assert me.post(base + "/api/clear", json={}).status_code == 200
    assert me.get(base + "/api/history").json()["events"] == []


# ------------------------------------------------------------------ the bridge is for the bot only
def test_bridge_needs_the_secret(base, me):
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        assert requests.get(base + "/bridge/next", headers=headers).status_code == 403
        assert requests.post(base + "/bridge/event", headers=headers,
                             json={"user": "jon", "type": "msg", "data": {}}).status_code == 403
    assert me.get(base + "/bridge/next").status_code == 403         # a logged-in browser is not the bot


def test_event_validation(bridge):
    with pytest.raises(BridgeError, match="400"):
        bridge.event("stranger", "msg", {"text": "x"})
    with pytest.raises(BridgeError, match="400"):
        bridge.event("jon", "script", {"text": "x"})
    with pytest.raises(BridgeError, match="413"):
        bridge.event("jon", "msg", {"text": "x" * 40000})


# ------------------------------------------------------------------ files
def test_file_upload_download_and_ranges(base, me, bridge):
    data = bytes(range(256)) * 40
    url = bridge.upload("jon", data, "video/mp4")
    assert url.startswith("/api/file/f_")
    r = me.get(base + url)
    assert r.content == data and r.headers["Content-Type"] == "video/mp4" and r.headers["Accept-Ranges"] == "bytes"
    part = me.get(base + url, headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == data[10:20] and part.headers["Content-Range"] == f"bytes 10-19/{len(data)}"
    tail = me.get(base + url, headers={"Range": "bytes=-5"})
    assert tail.status_code == 206 and tail.content == data[-5:]
    assert me.get(base + url, headers={"Range": "bytes=999999-"}).status_code == 416
    assert requests.get(base + url).status_code == 401              # not without the login cookie
    assert me.get(base + "/api/file/f_nothere0000000000").status_code == 404
    assert me.get(base + "/api/file/../../etc/passwd").status_code in (401, 404)


def test_upload_limits(bridge):
    with pytest.raises(BridgeError, match="400"):
        bridge.upload("jon", b"<script>", "text/html")             # only image/video
    with pytest.raises(BridgeError, match="413"):
        bridge.upload("jon", b"0" * (24 * 1024 * 1024 + 1), "video/mp4")
    with pytest.raises(BridgeError, match="400"):
        bridge.upload("stranger", b"x", "image/jpeg")


# ------------------------------------------------------------------ the whole thing, with the real bot loop
def test_bot_loop_answers_the_browser_with_thinking_events(base, me, bridge, isolated, monkeypatch):
    monkeypatch.setattr("agent.providers.llm.complete", lambda *a, **k: (time.sleep(0.3), "The answer is 42")[1])
    monkeypatch.setattr(loop_mod.gitstate, "commit_state", lambda *a, **k: False)
    drain(bridge)
    start = me.get(base + "/api/history").json()["last"]
    me.post(base + "/api/send", json={"text": "/ask what is 6 x 7"})
    me.post(base + "/api/send", json={"text": "/help"})
    me.post(base + "/api/send", json={"text": "/nonsense"})
    loop_mod.Loop(None, bridge, minutes=0.12).run()                 # ~7 seconds
    events = me.get(base + f"/api/events?after={start}").json()["events"]
    types = [e["type"] for e in events]
    assert types.count("user") == 3
    # messages are handled in parallel, so /help may land in between: the answer must sit INSIDE the animation
    i_start = types.index("think_start")
    i_stop = types.index("think_stop")
    i_answer = next(i for i, e in enumerate(events) if e["data"].get("text") == "The answer is 42")
    assert i_start < i_answer < i_stop
    tids = {e["data"]["tid"] for e in events if e["type"].startswith("think_")}
    assert len(tids) == 1                                            # one animation, closed properly
    texts = [e["data"]["text"] for e in events if e["type"] == "msg"]
    assert any("/addskill" in t for t in texts) and any("Unknown command" in t for t in texts)
