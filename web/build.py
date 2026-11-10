"""Builds web/worker.js (the single file you deploy) from web/src/worker.src.js + web/src/index.html.

    python web/build.py          # rewrite web/worker.js
    python web/build.py --check  # exit 1 if web/worker.js is out of date (used by the tests)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKEN = '/*__INDEX_HTML__*/ ""'


def build() -> str:
    src = (HERE / "src" / "worker.src.js").read_text(encoding="utf-8")
    html = (HERE / "src" / "index.html").read_text(encoding="utf-8")
    if TOKEN not in src:
        raise SystemExit("worker.src.js lost its INDEX_HTML placeholder")
    # json.dumps gives a valid JS string literal; </script> cannot appear in a JS file served as text/html? It
    # is only ever sent as a string body, so no extra escaping is needed.
    return src.replace(TOKEN, json.dumps(html, ensure_ascii=False))


def main(argv: list[str]) -> int:
    out = HERE / "worker.js"
    built = build()
    if "--check" in argv:
        ok = out.exists() and out.read_text(encoding="utf-8") == built
        print("worker.js is up to date" if ok else "worker.js is OUT OF DATE - run: python web/build.py")
        return 0 if ok else 1
    out.write_text(built, encoding="utf-8")
    print(f"wrote {out} ({len(built) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
