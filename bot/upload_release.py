"""Upload a video that is too big for Telegram (> 50 MB) to a GitHub Release and return the link.

Public repo  -> anyone with the link can download it.
Private repo -> the link only works for logged-in collaborators.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from agent.config import settings

log = logging.getLogger(__name__)


def upload_to_release(video: Path, tag: str = "videos") -> str | None:
    repo, token = settings.GITHUB_REPO, settings.GITHUB_TOKEN
    if not (repo and token):
        log.warning("release upload skipped: GITHUB_REPO / GITHUB_TOKEN missing")
        return None
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    base = f"https://api.github.com/repos/{repo}"
    try:
        r = requests.get(f"{base}/releases/tags/{tag}", headers=h, timeout=20)
        if r.status_code == 404:
            r = requests.post(f"{base}/releases", headers=h, timeout=20,
                              json={"tag_name": tag, "name": "Videos", "body": "Rendered by FreeVideoAgent"})
        r.raise_for_status()
        upload_url = r.json()["upload_url"].split("{")[0]
        name = f"{int(time.time())}_{video.name}"
        with open(video, "rb") as fh:
            up = requests.post(upload_url, params={"name": name}, timeout=900,
                               headers={**h, "Content-Type": "video/mp4"}, data=fh)
        up.raise_for_status()
        return up.json()["browser_download_url"]
    except Exception as e:  # noqa: BLE001
        log.error("release upload failed: %s", type(e).__name__)
        return None
