"""Minimal Telegram Bot API client built on requests.

Errors never contain the bot token (requests puts the URL, i.e. the token, into its exceptions).
"""
from __future__ import annotations

import io
import json
import logging
import time
from pathlib import Path

import requests

from agent.config import settings

log = logging.getLogger(__name__)
MAX_TEXT = 4000


class TelegramError(RuntimeError):
    pass


class Telegram:
    supports_thinking = True          # bot.thinking animates one message on this client

    def __init__(self, token: str | None = None) -> None:
        self.token = token or settings.TELEGRAM_BOT_TOKEN
        if not self.token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        self.base = f"https://api.telegram.org/bot{self.token}"
        self._edit_block_until = 0.0  # set when Telegram answers "Too Many Requests"

    # ------------------------------------------------------------------ core
    def call(self, method: str, data: dict | None = None, files: dict | None = None,
             timeout: int = 60, retries: int = 2):
        data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                for k, v in (data or {}).items() if v is not None}
        for attempt in range(retries + 1):
            try:
                r = requests.post(f"{self.base}/{method}", data=data, files=files, timeout=timeout)
            except requests.RequestException as e:
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise TelegramError(f"{method}: network error ({type(e).__name__})") from None
            try:
                body = r.json()
            except ValueError:
                raise TelegramError(f"{method}: HTTP {r.status_code}") from None
            if body.get("ok"):
                return body["result"]
            if r.status_code == 429 and attempt < retries:
                time.sleep(min(int(body.get("parameters", {}).get("retry_after", 2)), 30) + 1)
                continue
            raise TelegramError(f"{method}: {body.get('description', r.status_code)}")
        raise TelegramError(f"{method}: failed")

    # --------------------------------------------------------------- sending
    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        """Plain text (no parse_mode, so nothing can break on '<' or '&'). Long text is split."""
        text = text or "(empty)"
        chunks = [text[i:i + MAX_TEXT] for i in range(0, len(text), MAX_TEXT)]
        last = {}
        for i, ch in enumerate(chunks):
            last = self.call("sendMessage", {"chat_id": chat_id, "text": ch,
                                             "reply_markup": reply_markup if i == len(chunks) - 1 else None,
                                             "disable_web_page_preview": True})
        return last

    def edit_message_text(self, chat_id: int, message_id: int, text: str,
                          reply_markup: dict | None = None) -> None:
        if time.time() < self._edit_block_until:          # Telegram asked us to slow down
            return
        try:
            self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id,
                                          "text": text[:MAX_TEXT], "reply_markup": reply_markup,
                                          "disable_web_page_preview": True}, retries=0)
        except TelegramError as e:
            msg = str(e)
            if "retry after" in msg:
                digits = "".join(ch for ch in msg.rsplit("retry after", 1)[1] if ch.isdigit())
                self._edit_block_until = time.time() + min(int(digits or 5), 60)
            elif "not modified" not in msg:
                log.debug("edit failed: %s", e)

    def delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id}, retries=0)
        except TelegramError as e:
            log.debug("delete failed: %s", e)

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        """Shows "typing…" / "sending video…" at the top of the chat for ~5 seconds."""
        try:
            self.call("sendChatAction", {"chat_id": chat_id, "action": action}, retries=0, timeout=10)
        except TelegramError:
            pass

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]}, retries=0)
        except TelegramError:
            pass

    @staticmethod
    def _file(obj, default_name: str):
        if isinstance(obj, (str, Path)):
            p = Path(obj)
            return (p.name, open(p, "rb"))
        return (default_name, io.BytesIO(obj))

    def send_photo(self, chat_id: int, photo, caption: str = "") -> dict:
        name, fh = self._file(photo, "image.jpg")
        try:
            return self.call("sendPhoto", {"chat_id": chat_id, "caption": caption[:1000]},
                             files={"photo": (name, fh)}, timeout=120)
        finally:
            fh.close()

    def send_video(self, chat_id: int, video, caption: str = "", *, width=None, height=None,
                   duration=None) -> dict:
        name, fh = self._file(video, "video.mp4")
        try:
            return self.call("sendVideo", {"chat_id": chat_id, "caption": caption[:1000],
                                           "supports_streaming": True, "width": width, "height": height,
                                           "duration": int(duration) if duration else None},
                             files={"video": (name, fh)}, timeout=600)
        finally:
            fh.close()

    # --------------------------------------------------------------- receiving
    def get_updates(self, offset: int | None = None, timeout: int = 0, limit: int = 100) -> list[dict]:
        return self.call("getUpdates", {"offset": offset, "timeout": timeout, "limit": limit,
                                        "allowed_updates": ["message", "callback_query"]},
                         timeout=timeout + 30)

    def download_file(self, file_id: str, max_bytes: int = 5 * 1024 * 1024) -> bytes:
        info = self.call("getFile", {"file_id": file_id})
        if int(info.get("file_size") or 0) > max_bytes:
            raise TelegramError("file is too large")
        try:
            r = requests.get(f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}",
                             timeout=60, stream=True)
            r.raise_for_status()
            data = r.raw.read(max_bytes + 1, decode_content=True)
        except requests.RequestException as e:
            raise TelegramError(f"download failed ({type(e).__name__})") from None
        if len(data) > max_bytes:
            raise TelegramError("file is too large")
        return data


def kb(rows: list[list[tuple[str, str]]]) -> dict:
    """Inline keyboard from [[(label, callback_data), ...], ...]."""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}
