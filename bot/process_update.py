"""Handle ONE Telegram update delivered by the webhook relay (repository_dispatch -> bot_update.yml)."""
from __future__ import annotations

import json
import logging
import os
import sys

from agent.config import settings
from bot import gitstate
from bot.commands import handle_update
from bot.tg import Telegram


def main() -> int:
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
    path = os.getenv("GITHUB_EVENT_PATH", "")
    if not path:
        print("GITHUB_EVENT_PATH missing (this script is meant to run inside GitHub Actions).")
        return 2
    with open(path, encoding="utf-8") as fh:
        update = (json.load(fh).get("client_payload") or {}).get("update")
    if not isinstance(update, dict):
        print("No Telegram update in the event payload.")
        return 0
    handle_update(update, Telegram())
    return 0 if gitstate.commit_state("chore: bot state after telegram update [skip ci]") else 1


if __name__ == "__main__":
    sys.exit(main())
