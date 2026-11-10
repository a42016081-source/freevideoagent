"""Persistent memory: settings, feedback, notes and job history.

Two backends, chosen automatically:
  * Cloudflare D1 (agent.d1) when CLOUDFLARE_ACCOUNT_ID / D1_DATABASE_ID / CLOUDFLARE_API_TOKEN are set.
    Permanent, shared by every run. Errors are raised (never silently lost).
  * Small JSON files in state/ otherwise (committed back to the repo by bot/gitstate.py, because a
    GitHub Actions runner is thrown away after each run).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from agent import d1
from agent.config import settings


def _dir(name: str) -> Path:
    d = settings.STATE_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# ---------------------------------------------------------------- prefs
def get_prefs(user_id: int) -> dict:
    if d1.enabled():
        rows = d1.query("SELECT data FROM prefs WHERE user_id = ?", [int(user_id)])
        try:
            return json.loads(rows[0]["data"]) if rows else {}
        except (ValueError, KeyError):
            return {}
    return _read_json(_dir("prefs") / f"{int(user_id)}.json", {})


def _save_prefs(user_id: int, prefs: dict) -> None:
    if d1.enabled():
        d1.query("INSERT INTO prefs (user_id, data, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                 [int(user_id), json.dumps(prefs, ensure_ascii=False), int(time.time())])
    else:
        _write_json(_dir("prefs") / f"{int(user_id)}.json", prefs)


def set_pref(user_id: int, key: str, value) -> dict:
    prefs = get_prefs(user_id)
    if value is None:
        prefs.pop(key, None)
    else:
        prefs[key] = value
    _save_prefs(user_id, prefs)
    return prefs


def reset_prefs(user_id: int) -> None:
    if d1.enabled():
        d1.query("DELETE FROM prefs WHERE user_id = ?", [int(user_id)])
        return
    p = _dir("prefs") / f"{int(user_id)}.json"
    if p.exists():
        p.unlink()


# ------------------------------------------------------------- feedback
def add_feedback(user_id: int, text: str) -> list[str]:
    """Keep the last 5 feedback notes ('shorter', 'more energetic' ...).
    The planner reads them and applies them to future videos."""
    prefs = get_prefs(user_id)
    notes = list(prefs.get("feedback", []))
    notes.append(text.strip()[:200])
    prefs["feedback"] = notes[-5:]
    _save_prefs(user_id, prefs)
    return prefs["feedback"]


# ---------------------------------------------------------------- notes
MAX_NOTES = 30


def add_note(user_id: int, text: str) -> int:
    """A fact the agent should always remember ("my channel is @xyz", "I like short punchy lines").
    Returns the note id."""
    text = text.strip()[:500]
    if d1.enabled():
        d1.query("INSERT INTO notes (user_id, text, created_at) VALUES (?, ?, ?)",
                 [int(user_id), text, int(time.time())])
        return int(d1.query("SELECT MAX(id) AS i FROM notes WHERE user_id = ?", [int(user_id)])[0]["i"])
    path = _dir("notes") / f"{int(user_id)}.json"
    notes = _read_json(path, [])
    nid = max([n["id"] for n in notes], default=0) + 1
    notes.append({"id": nid, "text": text, "created_at": int(time.time())})
    _write_json(path, notes[-MAX_NOTES:])
    return nid


def list_notes(user_id: int, limit: int = MAX_NOTES) -> list[dict]:
    if d1.enabled():
        rows = d1.query("SELECT id, text, created_at FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                        [int(user_id), int(limit)])
        return list(reversed(rows))
    return _read_json(_dir("notes") / f"{int(user_id)}.json", [])[-limit:]


def delete_note(user_id: int, note_id: int) -> bool:
    if d1.enabled():
        before = len(list_notes(user_id))
        d1.query("DELETE FROM notes WHERE user_id = ? AND id = ?", [int(user_id), int(note_id)])
        return len(list_notes(user_id)) < before
    path = _dir("notes") / f"{int(user_id)}.json"
    notes = _read_json(path, [])
    kept = [n for n in notes if n["id"] != int(note_id)]
    if len(kept) == len(notes):
        return False
    _write_json(path, kept)
    return True


# ----------------------------------------------------------------- jobs
def new_job_id() -> int:
    return time.time_ns() // 1_000_000 % 10_000_000_000


def save_job(user_id: int, prompt: str, plan_json: str, output_path: str,
             status: str, job_id: int | None = None) -> int:
    jid = job_id or new_job_id()
    if d1.enabled():
        d1.query("INSERT OR REPLACE INTO jobs (id, user_id, prompt, plan, output, status, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 [jid, int(user_id), prompt[:1000], plan_json, output_path, status, int(time.time())])
        return jid
    _write_json(_dir("jobs") / f"{jid}.json", {
        "id": jid, "user_id": int(user_id), "prompt": prompt, "plan": plan_json,
        "output": output_path, "status": status, "created_at": time.time(),
    })
    return jid


def list_jobs(user_id: int, limit: int = 10) -> list[dict]:
    if d1.enabled():
        return d1.query("SELECT id, user_id, prompt, output, status, created_at FROM jobs "
                        "WHERE user_id = ? ORDER BY id DESC LIMIT ?", [int(user_id), int(limit)])
    rows = []
    for p in sorted(_dir("jobs").glob("*.json"), reverse=True):
        d = _read_json(p, None)
        if d and d.get("user_id") == int(user_id):
            rows.append(d)
        if len(rows) >= limit:
            break
    return rows


# --------------------------------------------------------------- offset
def get_offset() -> int:
    return int(_read_json(settings.STATE_DIR / "offset.json", {}).get("offset", 0))


def set_offset(offset: int) -> None:
    _write_json(settings.STATE_DIR / "offset.json", {"offset": int(offset)})
