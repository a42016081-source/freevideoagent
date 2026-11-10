"""One-shot poller for the cron workflow (bot_poll.yml).

Flow:  getUpdates -> handle each -> commit state (prefs/skills) -> ACK the updates to Telegram.
Telegram keeps unacknowledged updates for 24 h, so if anything fails before the ACK the messages
are simply handled again on the next run (nothing is lost).
Cannot be used while a webhook is set (use bot_update.yml + the Cloudflare relay for that).
"""
from __future__ import annotations

import logging
import sys

from agent.config import settings
from bot import gitstate
from bot.commands import handle_update
from bot.tg import Telegram, TelegramError

log = logging.getLogger("poll")


def main() -> int:
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
    if not settings.TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN is not set - nothing to do.")
        return 0
    tg = Telegram()
    try:
        updates = tg.get_updates(timeout=0)
    except TelegramError as e:
        if "webhook" in str(e).lower():
            print("A webhook is active, so getUpdates is disabled. Either remove it "
                  "(open https://api.telegram.org/bot<TOKEN>/deleteWebhook) or use the instant relay mode.")
            return 0
        print(f"getUpdates failed: {e}")
        return 1
    if not updates:
        print("No new messages.")
        return 0
    last = max(u["update_id"] for u in updates)
    for u in updates:
        try:
            handle_update(u, tg)
        except Exception:  # noqa: BLE001
            log.exception("update %s failed", u.get("update_id"))
    if not gitstate.commit_state("chore: bot state after telegram update [skip ci]"):
        print("State could not be saved; leaving updates unacknowledged so they are retried.")
        return 1
    tg.get_updates(offset=last + 1, timeout=0, limit=1)            # ACK
    print(f"Handled {len(updates)} update(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
