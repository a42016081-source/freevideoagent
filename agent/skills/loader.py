"""Skills: teach the agent new abilities from GitHub, a .zip or a .md file.

A skill is a folder:   skills/<name>/SKILL.md   (+ optional script.py / run.py / main.py,
requirements.txt, assets).  SKILL.md starts with a small front-matter block::

    ---
    name: motivational_quote
    description: one line - what it does
    when_to_use: when the planner should pick it
    inputs: quote, author
    outputs: image
    ---
    ...free-form instructions...

How a skill is used
  * its instructions are given to the planner (LLM) so it follows them when relevant;
  * if it has a script, a scene can use the script's image/video, and /runskill can run it.

Safety model (please read - this is NOT a real sandbox)
  1. Installing only DOWNLOADS files. Nothing from the skill is executed at install time.
  2. A static scan blocks obviously malicious patterns and lists risky ones as warnings.
  3. Every skill starts UNAPPROVED. You must approve it (Telegram button / CLI) before any script runs.
  4. Scripts run in a subprocess with a clean environment (no tokens/secrets), a timeout, and an
     output folder; output paths outside that folder are rejected.
  A malicious script that you approved can still do harm. Only approve code you have read.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import requests

from agent.config import settings
from agent.util import slugify

META_FILE = ".skill_meta.json"
SCRIPT_NAMES = ("script.py", "run.py", "main.py")
SKIP_DIRS = {".git", ".github", "node_modules", "__pycache__", ".venv", "venv"}
MAX_FILES, MAX_TOTAL_BYTES, MAX_ZIP_BYTES = 500, 30 * 1024 * 1024, 10 * 1024 * 1024
NAME_RE = re.compile(r"^[a-z0-9_]{1,48}$")


# ------------------------------------------------------------------ models
@dataclass
class Skill:
    name: str
    description: str = ""
    when_to_use: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    script: Path | None = None
    requirements: Path | None = None
    approved: bool = False
    path: Path | None = None
    source: str = ""


@dataclass
class Finding:
    level: str      # "block" | "warn"
    file: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.level.upper()}: {self.rule} ({self.file}:{self.line})"


@dataclass
class InstallResult:
    ok: bool
    message: str
    name: str = ""
    findings: list[Finding] = field(default_factory=list)

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warn"]


# --------------------------------------------------------------- static scan
BLOCK_RULES = [
    ("deletes root/home (rm -rf)", re.compile(r"rm\s+-[a-zA-Z]*[rR][a-zA-Z]*\s+(-[a-zA-Z]+\s+)*(/|~|\$HOME)(?=[\s'\"*;)&|]|$)")),
    ("pipes a download into a shell", re.compile(r"(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b")),
    ("decodes and executes hidden code", re.compile(r"(b64decode|base64\s+(-d|--decode)|fromhex)[^\n]*(exec|eval|\|\s*(ba)?sh)")),
    ("reads SSH/cloud credential files", re.compile(r"\.ssh/|\.aws/credentials|\.git-credentials|/etc/shadow|\.config/gh/")),
    ("reads the runner's git credentials", re.compile(r"\.git/config")),
    ("fork bomb", re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
]
WARN_RULES = [
    ("runs dynamic code (eval/exec)", re.compile(r"\b(eval|exec)\s*\(")),
    ("runs shell commands", re.compile(r"os\.system\s*\(|os\.popen\s*\(|shell\s*=\s*True|subprocess\.")),
    ("uses the network", re.compile(r"\b(requests|urllib|httpx|aiohttp|http\.client|socket)\b|\b(curl|wget)\s")),
    ("reads secret-looking environment variables",
     re.compile(r"os\.environ(?![^\n]*SKILL_)|getenv\s*\([^)]*(TOKEN|SECRET|PASSWORD|API_?KEY)", re.I)),
    ("deletes files", re.compile(r"shutil\.rmtree|os\.(remove|unlink|rmdir)|\brm\s+-")),
    ("unsafe deserialisation / native code", re.compile(r"pickle\.loads?\(|marshal\.loads?\(|ctypes")),
    ("imports modules dynamically", re.compile(r"__import__\s*\(|importlib\.")),
]
SCAN_SUFFIXES = {".py", ".sh", ".bash", ".js", ".mjs", ".ts"}


def static_scan(path: Path) -> list[Finding]:
    """Heuristic scan of every script under ``path``. Not a guarantee - a reading aid."""
    findings: list[Finding] = []
    path = Path(path)
    for f in sorted(path.rglob("*")):
        if not f.is_file() or f.is_symlink() or any(p in SKIP_DIRS for p in f.parts):
            continue
        rel = str(f.relative_to(path))
        if f.name == "requirements.txt":
            for i, line in enumerate(f.read_text("utf-8", "ignore").splitlines(), 1):
                if re.search(r"(git\+|https?://|--index-url|--extra-index-url|--find-links)", line):
                    findings.append(Finding("warn", rel, i, "installs from a custom URL/index"))
            continue
        if f.name == "setup.py":
            findings.append(Finding("warn", rel, 1, "has a setup.py (runs on pip install)"))
        if f.suffix.lower() not in SCAN_SUFFIXES:
            continue
        for i, line in enumerate(f.read_text("utf-8", "ignore").splitlines(), 1):
            for rule, rx in BLOCK_RULES:
                if rx.search(line):
                    findings.append(Finding("block", rel, i, rule))
            for rule, rx in WARN_RULES:
                if rx.search(line):
                    findings.append(Finding("warn", rel, i, rule))
    # de-duplicate (same rule+file) to keep reports short; keep first line number
    seen, out = set(), []
    for fd in findings:
        key = (fd.level, fd.file, fd.rule)
        if key not in seen:
            seen.add(key)
            out.append(fd)
    return out


# ------------------------------------------------------------------ helpers
def _d1_on() -> bool:
    from agent import d1
    return d1.enabled()


def skills_dir() -> Path:
    d = Path(settings.SKILLS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


def _skill_path(name: str) -> Path | None:
    """Resolve a skill folder from user input, refusing anything that is not a plain slug."""
    if not valid_name(name):
        return None
    p = skills_dir() / name
    return p if p.is_dir() else None


def _read_meta(path: Path) -> dict:
    try:
        return json.loads((path / META_FILE).read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_meta(path: Path, **kw) -> None:
    meta = _read_meta(path)
    meta.update(kw)
    (path / META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def parse_frontmatter(md: str) -> tuple[dict, str]:
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", md, re.DOTALL)
    if not m:
        return {}, md
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            k, v = line.split(":", 1)
            meta[k.strip().lower().replace("-", "_")] = v.strip().strip("\"'")
    return meta, m.group(2)


def _csv(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def scan_skills() -> dict[str, Skill]:
    reg: dict[str, Skill] = {}
    for d in sorted(skills_dir().iterdir()):
        md = d / "SKILL.md"
        if not (d.is_dir() and md.is_file() and valid_name(d.name)):
            continue
        meta, _ = parse_frontmatter(md.read_text("utf-8", "ignore"))
        sm = _read_meta(d)
        script = next((d / n for n in SCRIPT_NAMES if (d / n).is_file()), None)
        req = d / "requirements.txt"
        reg[d.name] = Skill(
            name=d.name, description=meta.get("description", ""), when_to_use=meta.get("when_to_use", ""),
            inputs=_csv(meta.get("inputs", "")), outputs=_csv(meta.get("outputs", "")),
            script=script, requirements=req if req.is_file() else None,
            approved=bool(sm.get("approved")), path=d, source=sm.get("source", ""))
    return reg


def get_skill_detail(name: str) -> str:
    s = scan_skills().get(name)
    if not s:
        return f"❌ Skill '{name}' not found."
    body = (s.path / "SKILL.md").read_text("utf-8", "ignore")
    return (f"📦 {s.name}  [{'✅ approved' if s.approved else '⏳ NOT approved'}]\n"
            f"{s.description}\n\nWhen to use: {s.when_to_use or '-'}\n"
            f"Inputs: {', '.join(s.inputs) or '-'}\nOutputs: {', '.join(s.outputs) or '-'}\n"
            f"Script: {s.script.name if s.script else 'none (instructions only)'}\n"
            f"Source: {s.source or '-'}\n\nSKILL.md (start):\n{body[:600]}")


def skills_prompt(max_chars: int = 6000) -> str:
    """Text for the planner: instructions of every APPROVED skill (empty string if none)."""
    parts = []
    for s in scan_skills().values():
        if not s.approved:
            continue
        _, body = parse_frontmatter((s.path / "SKILL.md").read_text("utf-8", "ignore"))
        head = f"### skill: {s.name}\n{s.description}\nWhen to use: {s.when_to_use or '-'}\n"
        if s.script:
            head += (f"Script skill. To use its picture/video for a scene, set that scene's "
                     f'"skill": {{"name": "{s.name}", "args": {{{", ".join(f"{i!r}: ..." for i in s.inputs)}}}}}.\n')
        parts.append(head + body.strip()[:1200])
    text = "\n\n".join(parts)
    return text[:max_chars]


# ------------------------------------------------------------------ staging
def _copy_tree(src: Path, dst: Path) -> None:
    """Copy files only (symlinks, VCS folders and caches are skipped)."""
    count = total = 0
    for f in src.rglob("*"):
        if f.is_symlink() or any(p in SKIP_DIRS for p in f.relative_to(src).parts):
            continue
        if f.is_file():
            count += 1
            total += f.stat().st_size
            if count > MAX_FILES or total > MAX_TOTAL_BYTES:
                raise ValueError("skill is too large (max 500 files / 30 MB)")
            target = dst / f.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)


def _find_root(src: Path) -> tuple[Path | None, str]:
    if (src / "SKILL.md").is_file():
        return src, ""
    found = [p.parent for p in src.rglob("SKILL.md")
             if not any(x in SKIP_DIRS for x in p.relative_to(src).parts)]
    if len(found) == 1:
        return found[0], ""
    if len(found) > 1:
        names = ", ".join(sorted(str(p.relative_to(src)) for p in found)[:8])
        return None, ("This repo holds several skills (" + names + "). "
                      "Send the URL of ONE folder, e.g. https://github.com/owner/repo/tree/main/<folder>")
    return src, "no-skill-md"


def stage_skill(src: Path, source: str, *, force: bool = False, name_hint: str = "") -> InstallResult:
    """Validate and copy a downloaded skill into skills/ as UNAPPROVED."""
    root, problem = _find_root(src)
    if root is None:
        return InstallResult(False, "❌ " + problem)
    work = Path(tempfile.mkdtemp(prefix="skill_stage_"))
    try:
        try:
            _copy_tree(root, work)
        except ValueError as e:
            return InstallResult(False, f"❌ {e}")
        md = work / "SKILL.md"
        if problem == "no-skill-md":                      # wrap a plain repo using its README
            readme = next((p for p in work.glob("README*") if p.is_file()), None)
            if not readme:
                return InstallResult(False, "❌ No SKILL.md and no README found.")
            text = readme.read_text("utf-8", "ignore")
            first = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), "Imported tool")
            md.write_text(f"---\nname: {slugify(name_hint or root.name)}\n"
                          f"description: {first[:180].replace(chr(10), ' ')}\n"
                          f"when_to_use: Imported from a README - read the instructions below\n---\n\n"
                          f"{text[:4000]}\n", encoding="utf-8")
        meta, _ = parse_frontmatter(md.read_text("utf-8", "ignore"))
        name = slugify(meta.get("name") or name_hint or root.name)
        if not valid_name(name):
            return InstallResult(False, "❌ Could not derive a valid skill name.")
        if not meta.get("description"):
            first = next((ln.strip("# ").strip() for ln in md.read_text("utf-8", "ignore").splitlines()
                          if ln.strip() and not ln.startswith("---")), name)
            body = md.read_text("utf-8", "ignore")
            md.write_text(f"---\nname: {name}\ndescription: {first[:180]}\n---\n{body}", encoding="utf-8")
        findings = static_scan(work)
        blocks = [f for f in findings if f.level == "block"]
        if blocks:
            return InstallResult(False, "🛑 Refused - dangerous patterns found:\n"
                                 + "\n".join(f"• {b}" for b in blocks[:8]), name, findings)
        dest = skills_dir() / name
        if dest.exists() and not force:
            return InstallResult(False, f"⚠️ A skill named '{name}' already exists. "
                                 f"Use /updateskill {name} (or /removeskill {name} first).", name, findings)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(work, dest)
        _write_meta(dest, approved=False, source=source, installed_at=int(time.time()))
        if _d1_on():                                       # permanent copy in Cloudflare D1
            from agent.skills import store
            try:
                _write_meta(dest, version=store.push(name, approved=False, source=source))
            except Exception as e:  # noqa: BLE001
                shutil.rmtree(dest, ignore_errors=True)
                return InstallResult(False, f"❌ Could not save the skill to D1: {e}", name, findings)
        return InstallResult(True, f"✅ Downloaded '{name}'. It is NOT active until you approve it.", name, findings)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ install
GITHUB_HOSTS = {"github.com", "www.github.com"}


def parse_github_url(url: str) -> dict | None:
    """Return {"kind": repo|raw, "owner", "repo", "ref", "path"} or None if it is not a GitHub URL."""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return None
    if p.scheme != "https":
        return None
    parts = [x for x in p.path.split("/") if x]
    if p.hostname == "raw.githubusercontent.com" and len(parts) >= 4:
        return {"kind": "raw", "owner": parts[0], "repo": parts[1], "ref": parts[2], "path": "/".join(parts[3:])}
    if p.hostname in GITHUB_HOSTS and len(parts) >= 2:
        owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
            return None
        if len(parts) >= 5 and parts[2] == "blob":
            return {"kind": "raw", "owner": owner, "repo": repo, "ref": parts[3], "path": "/".join(parts[4:])}
        if len(parts) >= 4 and parts[2] == "tree":
            return {"kind": "repo", "owner": owner, "repo": repo, "ref": parts[3], "path": "/".join(parts[4:])}
        return {"kind": "repo", "owner": owner, "repo": repo, "ref": "", "path": ""}
    return None


def install_from_markdown(text: str, name_hint: str, source: str, *, force: bool = False) -> InstallResult:
    if len(text) > 300_000:
        return InstallResult(False, "❌ SKILL.md is too large.")
    tmp = Path(tempfile.mkdtemp(prefix="skill_md_"))
    try:
        (tmp / "SKILL.md").write_text(text, encoding="utf-8")
        return stage_skill(tmp, source, force=force, name_hint=name_hint)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_from_zip(data: bytes, filename: str = "skill.zip", *, force: bool = False) -> InstallResult:
    if len(data) > MAX_ZIP_BYTES:
        return InstallResult(False, "❌ Zip is too large (max 10 MB).")
    tmp = Path(tempfile.mkdtemp(prefix="skill_zip_"))
    try:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return InstallResult(False, "❌ That is not a valid zip file.")
        infos = zf.infolist()
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            return InstallResult(False, "❌ Zip is too large (max 500 files / 30 MB unpacked).")
        base = tmp.resolve()
        for i in infos:
            mode = (i.external_attr >> 16) & 0o170000
            if mode == 0o120000:                              # symlink inside the archive
                continue
            target = (base / i.filename).resolve()
            if base != target and base not in target.parents:  # zip-slip (../ or absolute paths)
                return InstallResult(False, "🛑 Refused: zip contains paths outside its folder.")
            if i.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(i))
        stem = Path(filename).stem
        entries = [p for p in tmp.iterdir() if not p.name.startswith("__MACOSX")]
        src = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp
        return stage_skill(src, f"upload:{filename}", force=force, name_hint=stem)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _git_clone(url: str, dest: Path, ref: str = "") -> None:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(dest.parent), "GIT_TERMINAL_PROMPT": "0",
           "GIT_ASKPASS": "echo", "LANG": "C.UTF-8"}
    base = ["git", "-c", "core.symlinks=false", "-c", "protocol.allow=never",
            "-c", "protocol.https.allow=always", "clone", "--depth", "1", "--single-branch"]
    attempts = ([base + ["--branch", ref, "--", url, str(dest)]] if ref else []) + [base + ["--", url, str(dest)]]
    last = ""
    for cmd in attempts:
        shutil.rmtree(dest, ignore_errors=True)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env, cwd=dest.parent)
        if p.returncode == 0:
            return
        last = (p.stderr or p.stdout)[-300:]
    raise RuntimeError(last or "git clone failed")


def install_from_github(url: str, force: bool = False) -> InstallResult:
    """Repo, folder (/tree/<ref>/<path>), file (/blob/... or raw) - public GitHub only."""
    gh = parse_github_url(url)
    if not gh:
        return InstallResult(False, "❌ Please send a public https://github.com/... link "
                             "(repo, folder, or a SKILL.md file).")
    tmp = Path(tempfile.mkdtemp(prefix="skill_gh_"))
    try:
        if gh["kind"] == "raw":
            if not gh["path"].lower().endswith(".md"):
                return InstallResult(False, "❌ For a single file, link to a .md file (SKILL.md).")
            raw = f"https://raw.githubusercontent.com/{gh['owner']}/{gh['repo']}/{gh['ref']}/{gh['path']}"
            r = requests.get(raw, timeout=30)
            if r.status_code != 200:
                return InstallResult(False, f"❌ Could not download the file (HTTP {r.status_code}).")
            hint = gh["repo"] if Path(gh["path"]).name.lower() == "skill.md" else Path(gh["path"]).stem
            return install_from_markdown(r.text, hint, url, force=force)
        clone = tmp / "repo"
        try:
            _git_clone(f"https://github.com/{gh['owner']}/{gh['repo']}.git", clone, gh["ref"])
        except Exception as e:  # noqa: BLE001
            return InstallResult(False, f"❌ git clone failed: {e}")
        src = clone
        if gh["path"]:
            src = (clone / gh["path"]).resolve()
            if clone.resolve() not in src.parents and src != clone.resolve():
                return InstallResult(False, "❌ Invalid path in URL.")
            if not src.is_dir():
                return InstallResult(False, f"❌ Folder '{gh['path']}' not found in the repo.")
        return stage_skill(src, url, force=force, name_hint=Path(gh["path"]).name or gh["repo"])
    except requests.RequestException as e:
        return InstallResult(False, f"❌ Download failed: {type(e).__name__}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------- approval / update / remove
def approve_skill(name: str) -> str:
    p = _skill_path(name)
    if not p:
        return f"❌ Skill '{name}' not found."
    if _d1_on():
        from agent.skills import store
        try:
            store.set_approved(name, True)
        except Exception as e:  # noqa: BLE001
            return f"❌ Could not save approval to D1: {e}"
    _write_meta(p, approved=True, approved_at=int(time.time()))
    return f"✅ '{name}' approved. It can now be used by the planner and /runskill."


def reject_skill(name: str) -> str:
    return remove_skill(name)


def remove_skill(name: str) -> str:
    p = _skill_path(name)
    if not p:
        return f"❌ Skill '{name}' not found."
    if _d1_on():
        from agent.skills import store
        try:
            store.delete(name)
        except Exception as e:  # noqa: BLE001
            return f"❌ Could not remove it from D1: {e}"
    shutil.rmtree(p)
    return f"🗑 Removed skill '{name}'."


def update_skill(name: str, url: str | None = None) -> InstallResult:
    """Re-download from the recorded source (or a new URL). The new version needs approval again."""
    p = _skill_path(name)
    if not p:
        return InstallResult(False, f"❌ Skill '{name}' not found.")
    src = url or _read_meta(p).get("source", "")
    if not src.startswith("https://"):
        return InstallResult(False, f"⚠️ '{name}' came from an upload; send the new zip/.md file again, "
                             f"or use /updateskill {name} <github_url>.")
    res = install_from_github(src, force=True)
    if res.ok and res.name != name:
        res.message += f"\n(note: its internal name is '{res.name}', the old '{name}' was left as is)"
    return res


# --------------------------------------------------------------------- run
def _ensure_venv(skill: Skill) -> Path:
    """Per-skill virtualenv, created only AFTER approval, installed with a clean environment."""
    if not skill.requirements:
        return Path(sys.executable)
    venv = skill.path / ".venv"
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(skill.path), "LANG": "C.UTF-8"}
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True,
                       timeout=180, env=env)
        subprocess.run([str(py), "-m", "pip", "install", "--no-input", "-r", str(skill.requirements)],
                       check=True, capture_output=True, timeout=900, env=env, cwd=skill.path)
    return py


def run_skill(name: str, args: dict | None = None, *, timeout: int = 120, out_dir: Path | None = None) -> dict:
    """Run an approved skill. Returns {ok, kind, path, text, stdout, stderr, error}."""
    sk = scan_skills().get(name)
    if not sk:
        return {"ok": False, "error": f"Skill '{name}' not found."}
    if not sk.approved:
        return {"ok": False, "error": f"Skill '{name}' is not approved yet."}
    if not sk.script:
        return {"ok": False, "error": f"Skill '{name}' has no script (instructions only)."}
    out_dir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="skill_out_", dir=_runs_dir()))
    out_dir.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="skill_home_"))
    try:
        py = _ensure_venv(sk)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Could not install the skill's requirements: {type(e).__name__}"}
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "LANG": "C.UTF-8", "TZ": "UTC",
           "PYTHONDONTWRITEBYTECODE": "1", "SKILL_ARGS": json.dumps(args or {}, ensure_ascii=False),
           "SKILL_OUTPUT_DIR": str(out_dir.resolve())}
    try:
        p = subprocess.run([str(py), str(sk.script)], cwd=sk.path, capture_output=True, text=True,
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Skill timed out after {timeout}s."}
    finally:
        shutil.rmtree(home, ignore_errors=True)
    res: dict = {"ok": p.returncode == 0, "stdout": p.stdout[-1500:], "stderr": p.stderr[-800:]}
    last = next((ln for ln in reversed(p.stdout.strip().splitlines()) if ln.strip().startswith("{")), "")
    try:
        data = json.loads(last) if last else {}
    except json.JSONDecodeError:
        data = {}
    if isinstance(data, dict):
        res["kind"] = data.get("kind", "")
        res["text"] = str(data.get("text", ""))[:3000]
        if data.get("path"):
            out = Path(str(data["path"]))
            out = out if out.is_absolute() else out_dir / out
            out = out.resolve()
            if out.is_file() and out_dir.resolve() in out.parents:
                res["path"] = str(out)
                res["kind"] = res["kind"] or {".mp4": "video", ".mov": "video", ".mp3": "audio",
                                              ".wav": "audio"}.get(out.suffix.lower(), "image")
            else:
                res["ok"] = False
                res["error"] = "Skill returned a file outside its output folder (rejected)."
        if data.get("ok") is False:
            res["ok"] = False
    return res


def _runs_dir() -> Path:
    d = Path(settings.OUTPUT_DIR) / "skill_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d
