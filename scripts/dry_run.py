"""Offline smoke test: renders a short sample video with NO network and NO secrets.

    python scripts/dry_run.py

Needs only ffmpeg + the Python packages. Voice is silence (or espeak-ng if installed), pictures are
generated gradients, music is synthesised - so it proves the whole render pipeline works on this machine.
"""
import os
import sys
import time
from pathlib import Path

os.environ["RENDER_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import settings  # noqa: E402
from agent.planner import fallback_plan  # noqa: E402
from agent.tools.video_engine import render  # noqa: E402


def main() -> int:
    plan = fallback_plan("make a 10 second calm lofi reel in English", {"language": "en"})
    workdir = settings.OUTPUT_DIR / f"dryrun_{int(time.time())}"
    print(f"[dry-run] {len(plan.scenes)} scenes -> {workdir}")
    res = render(plan, workdir, offline=True, progress=lambda m: print("  ", m))
    mb = res.path.stat().st_size / 1048576
    print(f"[dry-run] OK: {res.path} ({mb:.2f} MB, {res.duration:.1f}s, captions={res.has_captions})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
