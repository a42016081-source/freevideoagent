"""The always-on bot:  python -m bot.loop

Runs inside ONE GitHub Actions job (bot_loop.yml) for LOOP_MINUTES (default 350, just under GitHub's
6 hour job limit), answering

* Telegram, by long-polling getUpdates (replies in a second or two, no cron delay), and
* the web chat, by long-polling the Cloudflare Worker (bot/web.py),

and then starts the same workflow again, so the bot is effectively never off. A cron entry in the
workflow (every 360 minutes) is the safety net if that hand-over ever fails.

Every message is handled in a small thread pool, so a slow /research never blocks the next message.
Telegram keeps updates for 24 h and the web inbox lives in D1, so nothing is lost in the few seconds
between two runs.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from agent import d1
from agent.config import settings
from bot import commands, dispatch, gitstate
from bot.tg import Telegram, TelegramError
from bot.web import BridgeError, WebBridge, WebTelegram, web_update

log = logging.getLogger("loop")
WORKFLOW = "bot_loop.yml"


class Loop:
    def __init__(self, tg: Telegram | None, bridge: WebBridge | None, minutes: float, workers: int = 4,
                 clock=time.monotonic) -> None:
        self.tg, self.bridge, self.clock = tg, bridge, clock
        self.deadline = clock() + minutes * 60
        self.stop = threading.Event()
        self.reason = "deadline"                       # deadline | signal
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="msg")
        self._commit_lock = threading.Lock()

    # ------------------------------------------------------------------ state
    def remaining(self) -> float:
        return self.deadline - self.clock()

    def alive(self) -> bool:
        return not self.stop.is_set() and self.remaining() > 0

    def commit(self) -> None:
        """Without D1 the notes / settings / skills live in the repo; with D1 this does nothing."""
        with self._commit_lock:
            try:
                gitstate.commit_state("chore: bot state [skip ci]")
            except Exception:  # noqa: BLE001
                log.exception("could not save state")

    # ------------------------------------------------------------------ handlers (run in the pool)
    def handle_telegram(self, update: dict) -> None:
        try:
            commands.handle_update(update, self.tg)
        except Exception:  # noqa: BLE001 - one bad update must never stop the bot
            log.exception("telegram update %s failed", update.get("update_id"))
        self.commit()

    def handle_web(self, item: dict) -> None:
        wtg = WebTelegram(self.bridge, item["user"], int(item["uid"]))
        try:
            commands.handle_update(web_update(item), wtg)
        except Exception as e:  # noqa: BLE001
            log.exception("web message %s failed", item.get("id"))
            try:
                wtg.send_message(None, f"⚠️ Something went wrong: {type(e).__name__}")
            except Exception:  # noqa: BLE001
                pass
        self.commit()

    # ------------------------------------------------------------------ pollers (one thread each)
    def telegram_loop(self) -> None:
        offset, errors = None, 0
        while self.alive():
            try:
                updates = self.tg.get_updates(offset=offset, timeout=int(min(25, max(self.remaining() - 5, 1))))
                errors = 0
            except TelegramError as e:
                errors += 1
                log.warning("getUpdates failed (%s)", e)
                if "webhook" in str(e).lower():
                    self.drop_webhook()
                self.stop.wait(min(5 * errors, 60))
                continue
            for u in updates:
                offset = u["update_id"] + 1
                self.pool.submit(self.handle_telegram, u)
        if offset is not None:                           # confirm what we took, so the next run does not repeat it
            try:
                self.tg.get_updates(offset=offset, timeout=0, limit=1)
            except TelegramError:
                pass

    def web_loop(self) -> None:
        errors = 0
        while self.alive():
            try:
                item = self.bridge.next(wait=int(min(15, max(self.remaining() - 5, 1))))
                errors = 0
            except BridgeError as e:
                errors += 1
                log.warning("web inbox unreachable (%s)", e)
                self.stop.wait(min(5 * errors, 60))
                continue
            if item:
                self.pool.submit(self.handle_web, item)

    def drop_webhook(self) -> None:
        """getUpdates does not work while a webhook (Instant mode relay) is set."""
        try:
            self.tg.call("deleteWebhook", {"drop_pending_updates": False}, retries=0)
            log.info("removed the Telegram webhook (this bot uses long polling now)")
        except TelegramError as e:
            log.warning("could not remove the webhook: %s", e)

    # ------------------------------------------------------------------ run
    def run(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, lambda *_: self._signal())
            except ValueError:                           # not in the main thread (tests)
                pass
        threads = []
        if self.tg:
            self.drop_webhook()
            threads.append(threading.Thread(target=self.telegram_loop, name="telegram", daemon=True))
        if self.bridge:
            threads.append(threading.Thread(target=self.web_loop, name="web", daemon=True))
        for t in threads:
            t.start()
        while self.alive():
            self.stop.wait(1.0)
        self.stop.set()
        for t in threads:
            t.join(timeout=60)
        self.pool.shutdown(wait=True)                    # let running answers finish (workflow timeout is the hard stop)

    def _signal(self) -> None:
        self.reason = "signal"
        self.stop.set()


def main() -> int:
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    tg = Telegram() if settings.TELEGRAM_BOT_TOKEN else None
    bridge = WebBridge() if WebBridge.enabled() else None
    if not (tg or bridge):
        print("Neither TELEGRAM_BOT_TOKEN nor WEB_BASE_URL + BRIDGE_SECRET is set - nothing to do.")
        return 0
    minutes = float(os.getenv("LOOP_MINUTES") or 350)
    log.info("bot started: telegram=%s web=%s d1=%s, running for %.0f minutes", bool(tg), bool(bridge),
             d1.enabled(), minutes)
    if not d1.enabled() and gitstate.in_ci():
        log.warning("Cloudflare D1 is NOT configured: settings, notes and skills are committed to the repo "
                    "(visible if the repo is public). Add the 3 CLOUDFLARE_/D1_ secrets.")
    loop = Loop(tg, bridge, minutes, workers=int(os.getenv("LOOP_WORKERS") or 4))
    loop.run()
    if loop.reason == "deadline" and os.getenv("LOOP_RESTART", "1") != "0":
        ok = dispatch.dispatch_workflow(WORKFLOW)
        log.info("restart requested: %s", "ok" if ok else "FAILED (the 6-hourly cron will start it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
