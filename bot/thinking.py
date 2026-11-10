"""A "Thinking…" indicator like Claude's - on Telegram AND on the web chat.

    with Thinking(tg, chat) as t:
        t.step("Searching the web")
        ...slow work...

* Telegram: ONE message that is edited every ~1.5 s (moving glyph + elapsed time + the last steps)
  while the "typing" chat action is repeated. When the work ends the message is removed
  (or replaced by ``final`` text, e.g. "✅ Video ready!").
* Web chat: the sink has native ``thinking_start / thinking_step / thinking_stop`` methods. The browser
  draws the animation itself (CSS), the server only sends small events.
* Any other sink (tests, local console) gets a silent no-op, so nothing else has to change.

Handlers that used to send "Working on it…" messages call :func:`status`, which turns into a step of
the running animation (or a normal message when there is no animation).
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time

log = logging.getLogger(__name__)

GLYPHS = "·✢✳✶✻✽✻✶✳✢"                     # the same family of sparkles Claude Code spins through
ACTION_EVERY = 4.0                          # Telegram shows "typing…" for ~5 s per call

_current: contextvars.ContextVar["Thinking | None"] = contextvars.ContextVar("thinking", default=None)


def fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(max(seconds, 0)), 60)
    return f"{m}:{s:02d}" if m else f"{s}s"


def current() -> "Thinking | None":
    t = _current.get()
    return t if t is not None and t.active else None


def status(tg, chat, text: str) -> None:
    """Show a progress line: as a step of the running animation, else as a normal message."""
    t = current()
    if t is not None:
        t.step(text)
    else:
        tg.send_message(chat, text)


class Thinking:
    def __init__(self, tg, chat, title: str = "Thinking", *, action: str = "typing",
                 interval: float = 1.5, max_steps: int = 6, clock=time.monotonic) -> None:
        self.tg, self.chat, self.title, self.action = tg, chat, title, action
        self.interval, self.max_steps, self.clock = interval, max_steps, clock
        self.enabled = bool(getattr(tg, "supports_thinking", False))
        self.native = self.enabled and hasattr(tg, "thinking_start")
        self.active = False
        self.steps: list[str] = []
        self.ticks = 0
        self._mid = None
        self._t0 = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._token = None

    # ------------------------------------------------------------------ rendering
    def elapsed(self) -> float:
        return self.clock() - self._t0

    def frame(self) -> str:
        head = f"{GLYPHS[self.ticks % len(GLYPHS)]} {self.title}… ({fmt_elapsed(self.elapsed())})"
        return head + ("\n\n" + "\n".join(self.steps[-self.max_steps:]) if self.steps else "")

    def _delay(self) -> float:
        return self.interval if self.elapsed() < 60 else self.interval * 2      # gentler on long jobs

    def _typing(self) -> None:
        if hasattr(self.tg, "send_chat_action"):
            try:
                self.tg.send_chat_action(self.chat, self.action)
            except Exception as e:  # noqa: BLE001 - decoration must never break real work
                log.debug("chat action failed: %s", e)

    def _run(self) -> None:
        last_action = self.clock()
        while not self._stop.wait(self._delay()):
            self.ticks += 1
            try:
                self.tg.edit_message_text(self.chat, self._mid, self.frame())
            except Exception as e:  # noqa: BLE001
                log.debug("thinking edit failed: %s", e)
            if self.clock() - last_action >= ACTION_EVERY:
                last_action = self.clock()
                self._typing()

    # ------------------------------------------------------------------ control
    def start(self) -> "Thinking":
        if not self.enabled or self.active:
            return self
        self._t0 = self.clock()
        try:
            if self.native:
                self.tg.thinking_start(self.chat, self.title)
            else:
                self._mid = self.tg.send_message(self.chat, self.frame())["message_id"]
                self._typing()
        except Exception as e:  # noqa: BLE001
            log.debug("thinking could not start: %s", e)
            return self
        self.active = True
        if not self.native:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="thinking", daemon=True)
            self._thread.start()
        return self

    def step(self, text: str) -> None:
        text = " ".join(str(text).split())[:120]
        if not self.active or not text or (self.steps and self.steps[-1] == text):
            return
        self.steps.append(text)
        del self.steps[:-50]
        if self.native:
            try:
                self.tg.thinking_step(self.chat, text)
            except Exception as e:  # noqa: BLE001
                log.debug("thinking step failed: %s", e)

    def stop(self, final: str | None = None) -> None:
        if not self.active:
            return
        self.active = False
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        try:
            if self.native:
                self.tg.thinking_stop(self.chat, final)
            elif self._mid is not None:
                if final is not None:
                    self.tg.edit_message_text(self.chat, self._mid, final)
                elif hasattr(self.tg, "delete_message"):
                    self.tg.delete_message(self.chat, self._mid)
                else:
                    self.tg.edit_message_text(self.chat, self._mid, f"✓ Done in {fmt_elapsed(self.elapsed())}")
        except Exception as e:  # noqa: BLE001
            log.debug("thinking could not finish: %s", e)

    def __enter__(self) -> "Thinking":
        self.start()
        self._token = _current.set(self)
        return self

    def __exit__(self, *exc) -> bool:
        if self._token is not None:
            _current.reset(self._token)
            self._token = None
        self.stop()
        return False
