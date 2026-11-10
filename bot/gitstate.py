"""Commit bot state (prefs, skills) back to the repo so it survives the throw-away Actions runner.

The token is passed only on the command line of `git push/pull` (never written to .git/config),
and is scrubbed from any output.
"""
from __future__ import annotations

import logging
import os
import subprocess

from agent.config import ROOT, settings

log = logging.getLogger(__name__)
PATHS = ["state", "skills"]


def _git(*args: str, check: bool = False, token: str = "") -> subprocess.CompletedProcess:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=120,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if token:
        p.stdout, p.stderr = p.stdout.replace(token, "***"), p.stderr.replace(token, "***")
    if check and p.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {(p.stderr or p.stdout)[-300:]}")
    return p


def in_ci() -> bool:
    return os.getenv("GITHUB_ACTIONS") == "true" and (ROOT / ".git").exists()


def commit_state(message: str = "chore: update bot state [skip ci]") -> bool:
    """True if there is nothing to save or the save succeeded. Outside CI this is a no-op."""
    from agent import d1
    if d1.enabled() or not in_ci():          # D1 already holds the state; outside CI there is nothing to push
        return True
    token, repo, branch = settings.GITHUB_TOKEN, settings.GITHUB_REPO, settings.GITHUB_BRANCH
    if not (token and repo):
        log.warning("cannot commit state: GITHUB_TOKEN / GITHUB_REPO missing")
        return False
    _git("add", "-A", "--", *PATHS)
    if _git("diff", "--cached", "--quiet").returncode == 0:
        return True
    _git("-c", "user.name=FreeVideoAgent", "-c", "user.email=bot@users.noreply.github.com",
         "commit", "-m", message, check=True)
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
    for attempt in range(5):
        if _git("push", remote, f"HEAD:{branch}", token=token).returncode == 0:
            return True
        log.info("push rejected (attempt %d), rebasing", attempt + 1)
        pull = _git("-c", "user.name=FreeVideoAgent", "-c", "user.email=bot@users.noreply.github.com",
                    "pull", "--rebase", remote, branch, token=token)
        if pull.returncode != 0:
            _git("rebase", "--abort")
            break
    log.error("could not push state")
    return False
