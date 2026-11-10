"""Connectors: plug other services into the agent (like ChatGPT "connectors").

To add one, drop a file ``connectors/<anything>.py`` that defines::

    NAME = "myservice"
    ENV_VARS = ["MYSERVICE_TOKEN"]        # what the user must set
    def connect() -> bool: ...            # True when configured / reachable
    def list_tools() -> list[str]: ...
    def call(tool: str, **kwargs): ...    # JSON-serialisable result

No registration needed - it is discovered automatically. Use it from Telegram with
``/connectors`` and ``/connector <name> <tool> {"json": "args"}``.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path
from types import ModuleType

log = logging.getLogger(__name__)
_REQUIRED = ("NAME", "connect", "list_tools", "call")


def discover() -> dict[str, ModuleType]:
    found: dict[str, ModuleType] = {}
    for m in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if m.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"connectors.{m.name}")
        except Exception as e:  # noqa: BLE001 - a broken connector must not break the others
            log.warning("connector %s failed to import: %s", m.name, e)
            continue
        if all(hasattr(mod, a) for a in _REQUIRED):
            found[mod.NAME] = mod
    return found


def status() -> list[dict]:
    rows = []
    for name, mod in sorted(discover().items()):
        try:
            ready = bool(mod.connect())
        except Exception:  # noqa: BLE001
            ready = False
        rows.append({"name": name, "ready": ready, "env": list(getattr(mod, "ENV_VARS", []))})
    return rows


def call(name: str, tool: str, **kwargs):
    mod = discover().get(name)
    if not mod:
        raise KeyError(f"unknown connector '{name}'")
    if not mod.connect():
        raise RuntimeError(f"connector '{name}' is not configured (set: {', '.join(getattr(mod, 'ENV_VARS', []))})")
    return mod.call(tool, **kwargs)
