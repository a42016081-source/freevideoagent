"""Best-effort: make sure a Devanagari font exists (needed for Hindi captions).

On the GitHub runner `apt install fonts-noto-core` already provides it; this script only downloads
Noto Sans Devanagari into assets/fonts/ when no Hindi-capable font is found.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import requests

URLS = {
    "NotoSansDevanagari-Bold.ttf": "https://github.com/notofonts/devanagari/raw/main/fonts/NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-Bold.ttf",
}


def has_hindi_font() -> bool:
    if not shutil.which("fc-list"):
        return False
    out = subprocess.run(["fc-list", ":lang=hi", "family"], capture_output=True, text=True).stdout
    return "devanagari" in out.lower()


def main() -> int:
    if has_hindi_font():
        print("Noto Sans Devanagari is installed - nothing to download.")
        return 0
    dest_dir = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name, url in URLS.items():
        dest = dest_dir / name
        if dest.exists():
            continue
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            dest.write_bytes(r.content)
            print(f"downloaded {name}")
        except Exception as e:  # noqa: BLE001
            print(f"could not download {name}: {type(e).__name__} - Hindi captions may show boxes", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
