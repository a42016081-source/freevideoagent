"""Run the bot as a long-polling process on your own computer / any free always-on host.

    python -m bot.local_run

Videos render locally (RENDER_MODE=local) unless GITHUB_REPO + GITHUB_TOKEN are set, in which case
they are rendered on GitHub Actions.
"""
from __future__ import annotations

import logging
import sys
import time

from agent.config import settings
from bot.commands import handle_update
from bot.tg import Telegram, TelegramError

log = logging.getLogger("local_run")


def main() -> int:
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
    tg = Telegram()
    if not settings.ALLOWED_USER_IDS:
        print("WARNING: ALLOWED_USER_IDS is empty, so every message will be refused. Set it in .env.")
    offset = None
    print("Bot is running. Press Ctrl+C to stop.")
    while True:
        try:
            for u in tg.get_updates(offset=offset, timeout=50):
                offset = u["update_id"] + 1
                try:
                    handle_update(u, tg)
                except Exception:  # noqa: BLE001
                    log.exception("update failed")
        except KeyboardInterrupt:
            return 0
        except TelegramError as e:
            log.warning("telegram error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
