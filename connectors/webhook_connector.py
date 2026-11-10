"""Generic REST / webhook connector - talk to ANY HTTP API (Zapier, Make, n8n, your own server).

Env:  WEBHOOK_URL (required), WEBHOOK_AUTH_HEADER (optional, e.g. "Authorization: Bearer xyz").
Tools:  post {payload: {...}} | get {params: {...}}
"""
from __future__ import annotations

import os

import requests

NAME = "webhook"
ENV_VARS = ["WEBHOOK_URL", "WEBHOOK_AUTH_HEADER"]


def connect() -> bool:
    return os.getenv("WEBHOOK_URL", "").startswith(("http://", "https://"))


def list_tools() -> list[str]:
    return ["post", "get"]


def _headers() -> dict:
    h = {"User-Agent": "FreeVideoAgent/1.0"}
    raw = os.getenv("WEBHOOK_AUTH_HEADER", "")
    if ":" in raw:
        k, v = raw.split(":", 1)
        h[k.strip()] = v.strip()
    return h


def call(tool: str, **kw):
    url = os.environ["WEBHOOK_URL"]
    if tool == "post":
        r = requests.post(url, json=kw.get("payload", {}), headers=_headers(), timeout=30)
    elif tool == "get":
        r = requests.get(url, params=kw.get("params", {}), headers=_headers(), timeout=30)
    else:
        raise ValueError("tools: post, get")
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return {"status": r.status_code, "text": r.text[:2000]}
