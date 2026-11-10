"""Bridge between the bot (GitHub Actions) and the web chat (the Cloudflare Worker in web/).

The bot has no public address, so it talks to the Worker over HTTPS with the shared BRIDGE_SECRET:

* ``WebBridge.next()``      long-polls for messages typed in the browser,
* ``WebBridge.event()``     pushes answers / thinking steps to the browser,
* ``WebBridge.upload()``    stores a video or image so the browser can play it.

``WebTelegram`` has the same methods as ``bot.tg.Telegram`` (send_message, edit_message_text, ...), so every
command handler works unchanged for the web chat.
"""
from __future__ import annotations

import logging
import mimetypes
import threading
import time
import uuid
from pathlib import Path

import requests

from agent.config import settings
from bot.tg import TelegramError

log = logging.getLogger(__name__)
MAX_TEXT = 20000


class BridgeError(RuntimeError):
    pass


# ------------------------------------------------------------------ channels
def web_chat_id(user: str, uid: int) -> str:
    return f"web:{user}:{int(uid)}"


def parse_channel(raw) -> tuple[str, int | str, int, str]:
    """CHAT_ID of a job -> (kind, chat, uid, web_user).  "123" -> telegram, "web:jon:123" -> web.
    Raises ValueError for garbage."""
    s = str(raw or "").strip()
    if s.startswith("web:"):
        user, _, uid = s[4:].rpartition(":")
        if not user or not uid.lstrip("-").isdigit():
            raise ValueError(f"bad web channel {s!r}")
        return "web", s, int(uid), user
    n = int(s or 0)
    return "telegram", n, n, ""


def web_update(item: dict) -> dict:
    """A message taken from the web inbox -> a Telegram-shaped update for bot.commands.handle_update."""
    uid, chat = int(item["uid"]), web_chat_id(item["user"], int(item["uid"]))
    if item.get("kind") == "callback":
        return {"callback_query": {"id": f"web{item['id']}", "from": {"id": uid}, "data": str(item.get("data", "")),
                                   "message": {"chat": {"id": chat}, "message_id": int(item.get("mid") or 0)}}}
    return {"update_id": item["id"], "message": {"message_id": item["id"], "from": {"id": uid},
                                                "chat": {"id": chat}, "text": str(item.get("text", ""))}}


# ------------------------------------------------------------------ HTTP client
class WebBridge:
    def __init__(self, base: str | None = None, secret: str | None = None) -> None:
        self.base = (base or settings.WEB_BASE_URL).rstrip("/")
        self.secret = secret or settings.BRIDGE_SECRET
        if not (self.base and self.secret):
            raise BridgeError("WEB_BASE_URL / BRIDGE_SECRET are not set")

    @staticmethod
    def enabled() -> bool:
        return bool(settings.WEB_BASE_URL and settings.BRIDGE_SECRET)

    def _req(self, method: str, path: str, *, retries: int = 2, timeout: int = 30, **kw):
        last = "request failed"
        headers = {"Authorization": f"Bearer {self.secret}", **kw.pop("headers", {})}
        for attempt in range(retries + 1):
            try:
                r = requests.request(method, self.base + path, timeout=timeout, headers=headers, **kw)
            except requests.RequestException as e:
                last = f"network error ({type(e).__name__})"        # never include the URL / headers
            else:
                if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    last = f"HTTP {r.status_code}"
                elif r.status_code >= 400:
                    raise BridgeError(f"{method} {path}: HTTP {r.status_code} {r.text[:160]}")
                else:
                    return r
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        raise BridgeError(f"{method} {path}: {last}")

    def next(self, wait: int = 15) -> dict | None:
        """Long-poll for the next message typed in the browser (None when nothing arrived)."""
        r = self._req("GET", "/bridge/next", params={"wait": wait}, timeout=wait + 20, retries=0)
        return None if r.status_code == 204 else r.json()

    def event(self, user: str, type_: str, data: dict | None = None) -> None:
        self._req("POST", "/bridge/event", json={"user": user, "type": type_, "data": data or {}})

    def upload(self, user: str, content: bytes | str | Path, mime: str) -> str:
        data = Path(content).read_bytes() if isinstance(content, (str, Path)) else content
        r = self._req("PUT", "/bridge/file", params={"user": user, "mime": mime}, data=data, timeout=300,
                      headers={"Content-Type": mime})
        return r.json()["url"]


# ------------------------------------------------------------------ Telegram look-alike
_mid_lock = threading.Lock()
_last_mid = 0


def _next_mid() -> int:
    global _last_mid
    with _mid_lock:
        _last_mid = max(int(time.time() * 1000), _last_mid + 1)
        return _last_mid


def _buttons(markup: dict | None) -> list | None:
    if not markup:
        return None
    return [[{"t": b["text"], "d": b["callback_data"]} for b in row] for row in markup.get("inline_keyboard", [])]


class WebTelegram:
    """Same surface as bot.tg.Telegram, but everything goes to the web chat of ONE user."""

    supports_thinking = True

    def __init__(self, bridge: WebBridge, user: str, uid: int = 0) -> None:
        self.bridge, self.user, self.uid = bridge, user, uid
        self.tid: str | None = None

    def _event(self, type_: str, **data) -> None:
        try:
            self.bridge.event(self.user, type_, data)
        except BridgeError as e:
            log.warning("web event %s failed: %s", type_, e)

    # ---- messages
    def send_message(self, chat_id, text: str, reply_markup: dict | None = None) -> dict:
        mid = _next_mid()
        self._event("msg", mid=mid, text=(text or "(empty)")[:MAX_TEXT], buttons=_buttons(reply_markup))
        return {"message_id": mid}

    def edit_message_text(self, chat_id, message_id, text: str, reply_markup: dict | None = None) -> None:
        self._event("edit", mid=message_id, text=(text or "")[:MAX_TEXT], buttons=_buttons(reply_markup))

    def delete_message(self, chat_id, message_id) -> None:
        self._event("del", mid=message_id)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        if text:
            self._event("toast", text=text[:180])

    # ---- files
    def _send_file(self, kind: str, obj, mime: str, caption: str, **extra) -> dict:
        try:
            url = self.bridge.upload(self.user, obj, mime)
        except BridgeError as e:
            log.warning("web upload failed: %s", e)
            return self.send_message(None, f"⚠️ The {kind} was made, but could not be stored for the web chat. "
                                           f"Check Telegram or the Actions artifact.\n{caption}")
        mid = _next_mid()
        self._event(kind, mid=mid, url=url, caption=caption[:1000], **{k: v for k, v in extra.items() if v})
        return {"message_id": mid}

    def send_photo(self, chat_id, photo, caption: str = "") -> dict:
        mime = "image/jpeg"
        if isinstance(photo, (str, Path)):
            mime = mimetypes.guess_type(str(photo))[0] or mime
        return self._send_file("image", photo, mime, caption)

    def send_video(self, chat_id, video, caption: str = "", *, width=None, height=None, duration=None) -> dict:
        return self._send_file("video", video, "video/mp4", caption, w=width, h=height,
                               duration=int(duration) if duration else None)

    def download_file(self, file_id: str, max_bytes: int = 0) -> bytes:
        raise TelegramError("File upload is only available in Telegram. In the web chat use /addskill <github url>.")

    # ---- thinking animation (drawn by the browser)
    def thinking_start(self, chat_id, title: str = "Thinking") -> None:
        self.tid = uuid.uuid4().hex[:8]
        self._event("think_start", tid=self.tid, title=title)

    def thinking_step(self, chat_id, text: str) -> None:
        if self.tid:
            self._event("think_step", tid=self.tid, text=text)

    def thinking_stop(self, chat_id, final: str | None = None) -> None:
        if self.tid:
            self._event("think_stop", tid=self.tid, final=final)
            self.tid = None
