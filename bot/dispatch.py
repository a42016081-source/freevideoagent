"""Start / list / cancel video jobs.

* On GitHub (GITHUB_REPO + GITHUB_TOKEN set): triggers the `video.yml` workflow.
* Locally (no repo configured): runs `python -m agent.run_job` as a background process.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

import requests

from agent.config import ROOT, settings

log = logging.getLogger(__name__)
API = "https://api.github.com"
WORKFLOW = "video.yml"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.GITHUB_TOKEN}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def github_ready() -> bool:
    return bool(settings.GITHUB_REPO and settings.GITHUB_TOKEN)


def _slim_prefs(prefs: dict) -> str:
    keep = {k: v for k, v in prefs.items() if k in ("voice", "language", "aspect_ratio", "music", "captions",
                                                    "watermark", "feedback", "notes")}
    s = json.dumps(keep, ensure_ascii=False)
    if len(s) < 6000:
        return s
    return json.dumps({k: v for k, v in keep.items() if k not in ("feedback", "notes")}, ensure_ascii=False)


def dispatch_video(prompt: str, chat_id: int, prefs: dict) -> tuple[bool, str]:
    prompt = prompt.strip()[:1000]
    if github_ready():
        try:
            r = requests.post(
                f"{API}/repos/{settings.GITHUB_REPO}/actions/workflows/{WORKFLOW}/dispatches",
                headers=_headers(), timeout=20,
                json={"ref": settings.GITHUB_BRANCH,
                      "inputs": {"prompt": prompt, "chat_id": str(chat_id), "prefs": _slim_prefs(prefs)}})
        except requests.RequestException as e:
            return False, f"Could not reach GitHub ({type(e).__name__})."
        if r.status_code == 204:
            return True, "✅ Queued on GitHub Actions. I will send the video here when it is ready (usually 5-15 min)."
        hint = {401: "token invalid/expired", 403: "token lacks 'Actions: write' permission",
                404: "workflow video.yml or repo/branch not found"}.get(r.status_code, f"HTTP {r.status_code}")
        return False, f"❌ GitHub refused the request: {hint}."
    if os.getenv("RENDER_MODE", "local") == "local":
        env = dict(os.environ, PROMPT=prompt, CHAT_ID=str(chat_id), PREFS=_slim_prefs(prefs))
        subprocess.Popen([sys.executable, "-m", "agent.run_job"], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True, "✅ Rendering locally. I will send the video here when it is ready."
    return False, "❌ GITHUB_REPO / GITHUB_TOKEN are not set, so I cannot start a render."


def dispatch_workflow(workflow: str, inputs: dict | None = None) -> bool:
    """Start any workflow_dispatch workflow of this repo (e.g. the always-on bot restarting itself)."""
    if not github_ready():
        return False
    try:
        r = requests.post(f"{API}/repos/{settings.GITHUB_REPO}/actions/workflows/{workflow}/dispatches",
                          headers=_headers(), timeout=20, json={"ref": settings.GITHUB_BRANCH, "inputs": inputs or {}})
    except requests.RequestException as e:
        log.warning("could not start %s (%s)", workflow, type(e).__name__)
        return False
    if r.status_code != 204:
        log.warning("could not start %s: HTTP %s", workflow, r.status_code)
    return r.status_code == 204


def list_runs(chat_id: int, limit: int = 6) -> list[dict]:
    """Recent video runs for this chat (the workflow's run-name contains 'chat <id>')."""
    if not github_ready():
        return []
    r = requests.get(f"{API}/repos/{settings.GITHUB_REPO}/actions/workflows/{WORKFLOW}/runs",
                     headers=_headers(), params={"per_page": 30}, timeout=20)
    r.raise_for_status()
    tag = f"chat {chat_id}"
    runs = [x for x in r.json().get("workflow_runs", []) if tag in (x.get("display_title") or x.get("name") or "")]
    return [{"id": x["id"], "status": x["status"], "conclusion": x.get("conclusion"),
             "title": (x.get("display_title") or "")[:60], "created": x.get("created_at", "")[:16]}
            for x in runs[:limit]]


def cancel_run(run_id: int, chat_id: int) -> str:
    if not github_ready():
        return "❌ Cancel needs GITHUB_REPO and GITHUB_TOKEN."
    mine = {r["id"] for r in list_runs(chat_id, 30)}
    if run_id not in mine:
        return f"❌ Run {run_id} is not one of your recent jobs (see /status)."
    r = requests.post(f"{API}/repos/{settings.GITHUB_REPO}/actions/runs/{run_id}/cancel",
                      headers=_headers(), timeout=20)
    return f"🛑 Cancel requested for run {run_id}." if r.status_code == 202 else f"❌ Could not cancel (HTTP {r.status_code})."
