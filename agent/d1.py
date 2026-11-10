"""Cloudflare D1 (serverless SQLite) over Cloudflare's REST API - the agent's permanent memory.

Why: a GitHub Actions runner is thrown away after every run. With D1 configured, your settings,
notes, job history and installed skills survive restarts, and every run sees the same data.

Setup (see README): create a D1 database, then set three values (GitHub Secrets / .env):
    CLOUDFLARE_ACCOUNT_ID   your account id
    D1_DATABASE_ID          the database's UUID
    CLOUDFLARE_API_TOKEN    custom token with ONLY the permission  Account -> D1 -> Edit

CLI:
    python -m agent.d1 status      # connectivity + table sizes
    python -m agent.d1 init        # create tables (also happens automatically on first use)
    python -m agent.d1 migrate     # copy local state/ and skills/ into D1 (run once)
"""
from __future__ import annotations

import logging
import sys
import time

import requests

from agent.config import settings

log = logging.getLogger(__name__)

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS prefs (user_id INTEGER PRIMARY KEY, data TEXT NOT NULL, updated_at INTEGER)",
    "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
    "text TEXT NOT NULL, created_at INTEGER)",
    "CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, id)",
    "CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, user_id INTEGER, prompt TEXT, plan TEXT, "
    "output TEXT, status TEXT, created_at INTEGER)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, id)",
    "CREATE TABLE IF NOT EXISTS skills (name TEXT PRIMARY KEY, version TEXT NOT NULL, "
    "approved INTEGER NOT NULL DEFAULT 0, source TEXT, size INTEGER, chunks INTEGER, updated_at INTEGER)",
    "CREATE TABLE IF NOT EXISTS skill_chunks (name TEXT NOT NULL, version TEXT NOT NULL, idx INTEGER NOT NULL, "
    "data TEXT NOT NULL, PRIMARY KEY (name, version, idx))",
]
_schema_ready: set[str] = set()


class D1Error(RuntimeError):
    pass


def enabled() -> bool:
    return bool(settings.CLOUDFLARE_ACCOUNT_ID and settings.D1_DATABASE_ID and settings.CLOUDFLARE_API_TOKEN)


def _url() -> str:
    return (f"{settings.CLOUDFLARE_API_BASE.rstrip('/')}/accounts/{settings.CLOUDFLARE_ACCOUNT_ID}"
            f"/d1/database/{settings.D1_DATABASE_ID}/query")


def _raw_query(sql: str, params: list | None = None) -> list[dict]:
    last = "request failed"
    for attempt in range(4):
        try:
            r = requests.post(_url(), json={"sql": sql, "params": params or []}, timeout=30,
                              headers={"Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}"})
        except requests.RequestException as e:
            last = f"network error ({type(e).__name__})"        # never include the URL/headers
        else:
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
            else:
                try:
                    body = r.json()
                except ValueError:
                    raise D1Error(f"D1: HTTP {r.status_code}, not JSON") from None
                if r.status_code == 200 and body.get("success"):
                    res = body.get("result") or []
                    return res[0].get("results", []) if res else []
                errs = "; ".join(str(e.get("message", e)) for e in body.get("errors", [])) or f"HTTP {r.status_code}"
                hint = {401: " (token invalid)", 403: " (token lacks D1 Edit permission)"}.get(r.status_code, "")
                raise D1Error(f"D1: {errs}{hint}")
        time.sleep(1.5 * (attempt + 1))
    raise D1Error(f"D1: {last}")


def ensure_schema() -> None:
    key = f"{settings.CLOUDFLARE_ACCOUNT_ID}/{settings.D1_DATABASE_ID}"
    if key in _schema_ready:
        return
    for stmt in SCHEMA:
        _raw_query(stmt)
    _schema_ready.add(key)


def query(sql: str, params: list | None = None) -> list[dict]:
    """Run one SQL statement (bound parameters only - never format user text into `sql`)."""
    if not enabled():
        raise D1Error("D1 is not configured")
    ensure_schema()
    return _raw_query(sql, params)


def status() -> dict:
    out = {}
    for t in ("prefs", "notes", "jobs", "skills"):
        out[t] = int(query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"])
    return out


def migrate_local() -> str:
    """Copy local state/ + skills/ into D1 (one-time)."""
    import json

    from agent.skills import store
    n_prefs = n_notes = 0
    prefs_dir = settings.STATE_DIR / "prefs"
    for p in sorted(prefs_dir.glob("*.json")) if prefs_dir.exists() else []:
        if not p.stem.isdigit():
            continue
        data = json.loads(p.read_text("utf-8"))
        query("INSERT INTO prefs (user_id, data, updated_at) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
              [int(p.stem), json.dumps(data, ensure_ascii=False), int(time.time())])
        n_prefs += 1
    notes_dir = settings.STATE_DIR / "notes"
    for p in sorted(notes_dir.glob("*.json")) if notes_dir.exists() else []:
        if p.stem.isdigit():
            for n in json.loads(p.read_text("utf-8")):
                query("INSERT INTO notes (user_id, text, created_at) VALUES (?, ?, ?)",
                      [int(p.stem), str(n.get("text", ""))[:500], int(n.get("created_at", time.time()))])
                n_notes += 1
    pushed = store.push_all_local()
    return f"migrated {n_prefs} user settings, {n_notes} notes, {pushed} skills"


def main(argv: list[str]) -> int:
    logging.basicConfig(level="INFO", format="%(message)s")
    cmd = argv[0] if argv else "status"
    if not enabled():
        print("D1 is not configured. Set CLOUDFLARE_ACCOUNT_ID, D1_DATABASE_ID and CLOUDFLARE_API_TOKEN.")
        return 2
    try:
        if cmd == "init":
            ensure_schema()
            print("tables ready")
        elif cmd == "migrate":
            print(migrate_local())
        else:
            print("connected. rows:", status())
    except D1Error as e:
        print(f"❌ {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
