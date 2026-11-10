"""MCP client connector (Model Context Protocol, "streamable HTTP" transport).

Env:  MCP_SERVER_URL  - the server's MCP endpoint, e.g. https://example.com/mcp
      MCP_API_KEY     - optional bearer token
Tools exposed by the remote server are listed with list_tools() and run with call(tool, **arguments).

Not supported: the stdio transport (a GitHub Actions job cannot keep a local process alive for you).
Run such a server behind an HTTP bridge, or use a hosted MCP server.
"""
from __future__ import annotations

import json
import os

import requests

NAME = "mcp"
ENV_VARS = ["MCP_SERVER_URL", "MCP_API_KEY"]
PROTOCOL = "2025-03-26"


class _Session:
    def __init__(self, url: str, key: str = "") -> None:
        self.url, self.key, self.sid, self._id = url, key, None, 0

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        return h

    @staticmethod
    def _parse(r: requests.Response, want_id):
        ctype = r.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        msg = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if msg.get("id") == want_id:
                        return msg
            raise RuntimeError("no response in event stream")
        return r.json()

    def rpc(self, method: str, params: dict | None = None, notify: bool = False):
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self._id += 1
            body["id"] = self._id
        r = requests.post(self.url, json=body, headers=self._headers(), timeout=30)
        r.raise_for_status()
        if "Mcp-Session-Id" in r.headers:
            self.sid = r.headers["Mcp-Session-Id"]
        if notify or r.status_code == 202 or not r.content:
            return None
        msg = self._parse(r, body["id"])
        if "error" in msg:
            raise RuntimeError(f"MCP error: {msg['error'].get('message', msg['error'])}")
        return msg.get("result")

    def start(self) -> "_Session":
        self.rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                "clientInfo": {"name": "FreeVideoAgent", "version": "1.0"}})
        self.rpc("notifications/initialized", notify=True)
        return self


def _session() -> _Session:
    return _Session(os.environ["MCP_SERVER_URL"], os.getenv("MCP_API_KEY", "")).start()


def connect() -> bool:
    return os.getenv("MCP_SERVER_URL", "").startswith(("http://", "https://"))


def list_tools() -> list[str]:
    try:
        res = _session().rpc("tools/list") or {}
        return [t["name"] for t in res.get("tools", [])]
    except Exception:  # noqa: BLE001
        return []


def call(tool: str, **kwargs):
    res = _session().rpc("tools/call", {"name": tool, "arguments": kwargs}) or {}
    text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
    return {"is_error": bool(res.get("isError")), "text": text, "raw": res}
