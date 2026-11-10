"""Keep skills in Cloudflare D1 so they survive restarts (D1 is the source of truth when enabled).

A skill folder is packed into a zip, base64-encoded and stored in small chunks (D1 limits a single
statement to ~100 KB). `version` is a content hash, so unchanged skills are never re-downloaded.

    install/approve/remove  -> push() / set_approved() / delete()      (called by loader.py)
    start of every run      -> sync_down()  makes the local skills/ folder match D1
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from agent import d1
from agent.config import settings

log = logging.getLogger(__name__)
CHUNK = 45_000                      # raw bytes per row -> ~60 KB of base64
MAX_PACK = 3 * 1024 * 1024
SKIP = {".venv", "__pycache__", ".git"}
META = ".skill_meta.json"


def _files(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*")
                  if p.is_file() and not p.is_symlink() and p.name != META
                  and not any(x in SKIP for x in p.relative_to(path).parts))


def fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    for f in _files(path):
        h.update(str(f.relative_to(path)).encode() + b"\0")
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def pack(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in _files(path):
            z.writestr(zipfile.ZipInfo(str(f.relative_to(path)), (2020, 1, 1, 0, 0, 0)), f.read_bytes())
    return buf.getvalue()


def _safe_unpack(data: bytes, dest: Path) -> None:
    base = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if len(infos) > 500 or sum(i.file_size for i in infos) > 30 * 1024 * 1024:
            raise ValueError("stored skill is too large")
        for i in infos:
            target = (base / i.filename).resolve()
            if base != target and base not in target.parents:
                raise ValueError("unsafe path in stored skill")
            if i.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(i))


# ------------------------------------------------------------------ writes
def push(name: str, *, approved: bool, source: str = "") -> str:
    """Upload a local skill folder. Returns its version. Raises d1.D1Error / ValueError."""
    from agent.skills.loader import skills_dir
    path = skills_dir() / name
    version = fingerprint(path)
    data = pack(path)
    if len(data) > MAX_PACK:
        raise ValueError(f"skill is {len(data) // 1024} KB packed; D1 storage limit is {MAX_PACK // 1024} KB")
    b64 = base64.b64encode(data).decode()
    step = CHUNK * 4 // 3 // 4 * 4
    parts = [b64[i:i + step] for i in range(0, len(b64), step)] or [""]
    existing = d1.query("SELECT version FROM skills WHERE name = ?", [name])
    if not (existing and existing[0]["version"] == version):
        for idx, part in enumerate(parts):
            d1.query("INSERT OR REPLACE INTO skill_chunks (name, version, idx, data) VALUES (?, ?, ?, ?)",
                     [name, version, idx, part])
    d1.query("INSERT INTO skills (name, version, approved, source, size, chunks, updated_at) "
             "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET version = excluded.version, "
             "approved = excluded.approved, source = excluded.source, size = excluded.size, "
             "chunks = excluded.chunks, updated_at = excluded.updated_at",
             [name, version, int(approved), source, len(data), len(parts), int(time.time())])
    d1.query("DELETE FROM skill_chunks WHERE name = ? AND version != ?", [name, version])
    return version


def set_approved(name: str, approved: bool) -> None:
    d1.query("UPDATE skills SET approved = ?, updated_at = ? WHERE name = ?", [int(approved), int(time.time()), name])


def delete(name: str) -> None:
    d1.query("DELETE FROM skill_chunks WHERE name = ?", [name])
    d1.query("DELETE FROM skills WHERE name = ?", [name])


def push_all_local() -> int:
    """One-time migration: upload every local (non-builtin) skill."""
    from agent.skills import loader
    n = 0
    for name, s in loader.scan_skills().items():
        meta = loader._read_meta(s.path)
        if meta.get("source") == "builtin":
            continue
        ver = push(name, approved=s.approved, source=meta.get("source", ""))
        loader._write_meta(s.path, version=ver)
        n += 1
    return n


# ------------------------------------------------------------------- sync
def sync_down() -> dict:
    """Make local skills/ match D1. Builtin skills shipped in the repo are left alone."""
    from agent.skills import loader
    if not d1.enabled():
        return {"enabled": False}
    out = {"enabled": True, "downloaded": 0, "removed": 0, "failed": []}
    rows = {r["name"]: r for r in d1.query("SELECT name, version, approved, source FROM skills")}
    root = loader.skills_dir()
    for name, row in rows.items():
        if not loader.valid_name(name):
            continue
        local = root / name
        meta = loader._read_meta(local) if local.is_dir() else {}
        if local.is_dir() and meta.get("version") == row["version"]:
            if bool(meta.get("approved")) != bool(row["approved"]):
                loader._write_meta(local, approved=bool(row["approved"]))
            continue
        try:
            chunks = d1.query("SELECT data FROM skill_chunks WHERE name = ? AND version = ? ORDER BY idx",
                              [name, row["version"]])
            blob = base64.b64decode("".join(c["data"] for c in chunks))
            tmp = Path(tempfile.mkdtemp(prefix="skill_sync_"))
            try:
                _safe_unpack(blob, tmp)
                if fingerprint(tmp) != row["version"]:
                    raise ValueError("integrity check failed (content does not match its version)")
                if local.exists():
                    shutil.rmtree(local)
                shutil.copytree(tmp, local)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            (local / META).write_text(json.dumps({"approved": bool(row["approved"]), "source": row["source"] or "",
                                                  "version": row["version"]}, indent=2), encoding="utf-8")
            out["downloaded"] += 1
        except Exception as e:  # noqa: BLE001 - one broken skill must not block the others
            log.warning("could not sync skill '%s': %s", name, e)
            out["failed"].append(name)
    for d in list(root.iterdir()):                       # D1 is the source of truth
        if d.is_dir() and d.name not in rows and loader.valid_name(d.name) \
                and loader._read_meta(d).get("source") != "builtin":
            shutil.rmtree(d, ignore_errors=True)
            out["removed"] += 1
    return out
