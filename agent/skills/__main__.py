"""Command line for skills (also used by the install_skill.yml workflow).

    python -m agent.skills list
    python -m agent.skills install <github-url>
    python -m agent.skills approve <name>
    python -m agent.skills remove <name>
    python -m agent.skills run <name> '{"quote": "hello"}'
"""
from __future__ import annotations

import json
import sys

from agent.skills import loader, store  # noqa: F401


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    from agent import d1
    if d1.enabled():
        print("sync from D1:", store.sync_down())
    if cmd == "list":
        for s in loader.scan_skills().values():
            print(f"{'[approved]' if s.approved else '[PENDING] '} {s.name}: {s.description}")
        return 0
    if cmd == "install" and rest:
        res = loader.install_from_github(rest[0], force="--force" in rest)
        print(res.message)
        for f in res.warnings:
            print("  ", f)
        return 0 if res.ok else 1
    if cmd == "approve" and rest:
        print(loader.approve_skill(rest[0]))
        return 0
    if cmd == "remove" and rest:
        print(loader.remove_skill(rest[0]))
        return 0
    if cmd == "run" and rest:
        args = json.loads(rest[1]) if len(rest) > 1 else {}
        out = loader.run_skill(rest[0], args)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out.get("ok") else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
