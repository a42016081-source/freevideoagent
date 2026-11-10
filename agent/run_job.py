"""One video job, start to finish:  python -m agent.run_job

Environment:  PROMPT (required), CHAT_ID (Telegram chat to report to), PREFS (JSON user settings),
RENDER_OFFLINE=1 (never touch the network - used by the dry run).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from agent import memory
from agent import skills as skill_lib
from agent.config import settings
from agent.planner import fallback_plan, make_plan, plan_to_json
from agent.tools import thumbnail, viral
from agent.tools.video_engine import SIZES, render
from agent.util import shorten
from bot.thinking import Thinking

log = logging.getLogger("run_job")


class Progress:
    """The "Thinking…" animation of a video job (Telegram message edits / web chat events).

    Silent when there is no chat. ``step`` adds a line, ``done`` ends it with a final text.
    """

    def __init__(self, tg, chat_id, title: str = "Making your video") -> None:
        self.thinking = Thinking(tg, chat_id, title, action="upload_video", max_steps=7) if tg else None

    def step(self, line: str) -> None:
        log.info(line)
        if self.thinking:
            if not self.thinking.active:
                self.thinking.start()
            self.thinking.step(line)

    def done(self, text: str) -> None:
        if self.thinking:
            self.thinking.stop(final=text)


def _load_prefs(user_id: int) -> dict:
    raw = os.getenv("PREFS", "").strip()
    if raw:
        try:
            val = json.loads(raw)
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            log.warning("PREFS is not valid JSON; ignoring")
    return memory.get_prefs(user_id) if user_id else {}


def _run_skills(plan, workdir: Path, prog: Progress) -> dict[int, tuple[str, Path]]:
    """Let skills produce the visual of the scenes that asked for one."""
    approved = {n for n, s in skill_lib.scan_skills().items() if s.approved and s.script}
    out: dict[int, tuple[str, Path]] = {}
    used: list[str] = []
    for i, sc in enumerate(plan.scenes):
        if not sc.skill:
            continue
        name = sc.skill["name"]
        if name not in approved:
            log.warning("scene %d asked for skill '%s' which is missing/not approved - skipped", i, name)
            continue
        prog.step(f"🧩 Skill '{name}' for scene {i + 1}")
        res = skill_lib.run_skill(name, sc.skill.get("args", {}), out_dir=workdir / f"skill_{i}")
        if res.get("ok") and res.get("path") and res.get("kind") in ("image", "video"):
            out[i] = (res["kind"], Path(res["path"]))
            used.append(name)
        else:
            log.warning("skill '%s' failed: %s", name, res.get("error") or res.get("stderr"))
    plan.skills_used = sorted(set(used))
    return out


def _clean(workdir: Path, keep: set[str]) -> None:
    if settings.KEEP_WORK:
        return
    for p in workdir.iterdir():
        if p.name in keep:
            continue
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)


def run(prompt: str, chat_id, prefs: dict, tg=None, uid: int | None = None) -> Path:
    """``chat_id`` is where to report (Telegram chat id or "web:<user>:<uid>"); ``uid`` owns the memory."""
    prog = Progress(tg, chat_id)
    try:
        return _run(prompt, chat_id, prefs, tg, prog, uid if uid is not None else (chat_id if isinstance(chat_id, int) else 0))
    except BaseException:
        prog.done("❌ Video failed")
        raise


def _run(prompt: str, chat_id, prefs: dict, tg, prog: Progress, uid: int) -> Path:
    job_id = int(os.getenv("GITHUB_RUN_ID") or memory.new_job_id())
    workdir = settings.OUTPUT_DIR / f"job_{job_id}"
    workdir.mkdir(parents=True, exist_ok=True)

    prog.step("🧠 Writing the script…")
    if settings.RENDER_OFFLINE:
        plan = fallback_plan(prompt, prefs)
    else:
        plan = make_plan(prompt, prefs, skill_lib.skills_prompt())
    prog.step(f"✍️ Plan ready: “{shorten(plan.title, 40)}” ({len(plan.scenes)} scenes, via {plan.source})")
    (workdir / "plan.json").write_text(plan_to_json(plan), encoding="utf-8")

    custom = _run_skills(plan, workdir, prog)
    result = render(plan, workdir, watermark=prefs.get("watermark") or None,
                    use_music=prefs.get("music", True), with_captions=prefs.get("captions", True),
                    progress=prog.step, custom_visuals=custom)

    w, h = SIZES[plan.aspect_ratio.value]
    final = settings.OUTPUT_DIR / f"{job_id}.mp4"
    shutil.copy2(result.path, final)
    thumb = thumbnail.make_thumbnail(final, plan.title, settings.OUTPUT_DIR / f"{job_id}_thumb.jpg", w, h)

    tags = viral.optimize_hashtags(plan)
    report = viral.retention_report(plan, result.duration, result.has_captions)
    titles = viral.ab_titles(plan)
    posting = viral.best_posting_time(plan.language)
    details = (f"{plan.title}\n\n{plan.description}\n\n{' '.join(tags)}\n\n"
               f"A/B titles:\n" + "\n".join(f"- {t}" for t in titles) +
               f"\n\nRetention check:\n{report}\n\n{posting}\n\n"
               f"Voice: {', '.join(sorted(set(result.tts_providers)))} | visuals: "
               f"{', '.join(sorted(set(result.visual_sources)))} | music: {result.music}"
               f"{' | skills: ' + ', '.join(plan.skills_used) if plan.skills_used else ''}")
    (settings.OUTPUT_DIR / f"{job_id}_report.txt").write_text(details, encoding="utf-8")
    memory.save_job(uid, prompt, plan_to_json(plan), str(final), "done", job_id)
    _clean(workdir, keep={"plan.json", "captions.ass"})
    size_mb = final.stat().st_size / 1048576
    log.info("done: %s (%.1f MB, %.1fs)", final, size_mb, result.duration)

    if tg and chat_id:
        link = None
        if size_mb > 49:
            from bot.upload_release import upload_to_release
            link = upload_to_release(final)
        prog.done("✅ Video ready!")
        if link:
            tg.send_message(chat_id, f"📥 The video is {size_mb:.0f} MB - too big for Telegram.\n{link}")
        else:
            tg.send_video(chat_id, final, caption=f"{plan.title}\n{' '.join(tags)}"[:1000], width=w, height=h,
                          duration=result.duration)
        if thumb and thumb.exists():
            tg.send_photo(chat_id, thumb, caption="Thumbnail")
        tg.send_message(chat_id, details)
    print(f"ARTIFACT_PATH={final}")
    return final


def main() -> int:
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    prompt = os.getenv("PROMPT", "").strip()
    if not prompt:
        log.error("PROMPT is empty")
        return 1
    from bot.web import WebBridge, WebTelegram, parse_channel
    try:
        kind, chat_id, uid, web_user = parse_channel(os.getenv("CHAT_ID", ""))
    except ValueError:
        kind, chat_id, uid, web_user = "telegram", 0, 0, ""
    from agent import d1
    if d1.enabled():
        try:
            skill_lib.store.sync_down()
        except Exception as e:  # noqa: BLE001
            log.warning("skill sync from D1 failed: %s", e)
    prefs = _load_prefs(uid)
    if uid and "notes" not in prefs:
        try:
            prefs["notes"] = [n["text"] for n in memory.list_notes(uid, 10)]
        except Exception as e:  # noqa: BLE001
            log.warning("could not load notes: %s", e)
    tg = None
    if kind == "web":
        if WebBridge.enabled():
            tg = WebTelegram(WebBridge(), web_user, uid)
            settings.MAX_UPLOAD_MB = min(settings.MAX_UPLOAD_MB, 24)      # the web store keeps files up to 24 MB
        else:
            log.warning("web chat job but WEB_BASE_URL / BRIDGE_SECRET are not set: result cannot be delivered")
    elif chat_id and settings.TELEGRAM_BOT_TOKEN:
        from bot.tg import Telegram
        tg = Telegram()
    try:
        run(prompt, chat_id, prefs, tg, uid)
        return 0
    except Exception as e:  # noqa: BLE001
        log.exception("job failed")
        if tg:
            try:
                tg.send_message(chat_id, f"❌ Video failed: {type(e).__name__}: {shorten(str(e), 500)}\n"
                                f"Check the Actions log for details.")
            except Exception:  # noqa: BLE001
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
