"""GitHub connector (REST API). Needs GITHUB_TOKEN (on Actions this is provided automatically)."""
from __future__ import annotations

import requests

from agent.config import settings

NAME = "github"
ENV_VARS = ["GITHUB_TOKEN"]
API = "https://api.github.com"
TOOLS = ["get_repo", "list_issues", "create_issue", "list_workflow_runs"]


def connect() -> bool:
    return bool(settings.GITHUB_TOKEN)


def list_tools() -> list[str]:
    return list(TOOLS)


def _h() -> dict:
    return {"Authorization": f"Bearer {settings.GITHUB_TOKEN}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def call(tool: str, **kw):
    repo = kw.get("repo") or settings.GITHUB_REPO
    if tool not in TOOLS:
        raise ValueError(f"unknown tool '{tool}'. Available: {', '.join(TOOLS)}")
    if not repo or "/" not in repo:
        raise ValueError("pass repo as 'owner/name' (or set GITHUB_REPO)")
    if tool == "get_repo":
        r = requests.get(f"{API}/repos/{repo}", headers=_h(), timeout=20)
    elif tool == "list_issues":
        r = requests.get(f"{API}/repos/{repo}/issues", headers=_h(), timeout=20,
                         params={"state": kw.get("state", "open"), "per_page": 10})
    elif tool == "create_issue":
        r = requests.post(f"{API}/repos/{repo}/issues", headers=_h(), timeout=20,
                          json={"title": kw["title"], "body": kw.get("body", "")})
    else:
        r = requests.get(f"{API}/repos/{repo}/actions/runs", headers=_h(), timeout=20, params={"per_page": 10})
    r.raise_for_status()
    return r.json()
