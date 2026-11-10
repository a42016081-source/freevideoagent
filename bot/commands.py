"""All Telegram commands in one place.

`handle_update(update, tg)` takes ONE raw Telegram update and replies through `tg`. The same code
runs from the 5-minute cron poller, from the instant webhook relay, and from a local long-poll loop.
"""
from __future__ import annotations

import io
import json
import logging

from agent import d1, memory, router
from agent import skills as sk
from agent.config import settings
from agent.providers.registry import enabled_providers
from agent.util import shorten
from bot import dispatch
from bot.tg import Telegram, TelegramError, kb
from bot.thinking import Thinking, status

log = logging.getLogger(__name__)

HELP = """🎬 FreeVideoAgent

Create
/video <idea> - make a short video (or just type what you want)
/image <prompt> - make an image
/ask <question> - chat / writing / code help
/research <topic> - web research with sources
/trends - what is trending in India today

Teach me new skills
/addskill <github url> - repo, folder or SKILL.md link
(or send me a .zip / .md file)
/skills - list      /skill <name> - details
/runskill <name> {json args} - test a skill
/updateskill <name> [url]   /removeskill <name>

Connectors
/connectors - list      /connector <name> <tool> {json args}

Jobs & settings
/status - recent video jobs   /cancel <run id>
/settings - voice, language, ratio, music, captions
/watermark <text|off>   /feedback <text> ("shorter", "more energetic")

Memory (I never forget these)
/remember <fact> - e.g. "my channel is @xyz, I like short punchy lines"
/notes - list      /forget <id>
/providers - which optional services are on
"""


# --------------------------------------------------------------------- utils
def _authorized(uid: int) -> bool:
    return uid in settings.ALLOWED_USER_IDS


def _args(text: str) -> tuple[str, str]:
    """'/cmd@bot  a b' -> ('cmd', 'a b')"""
    head, _, rest = text.strip().partition(" ")
    return head[1:].split("@")[0].lower(), rest.strip()


def _json_arg(raw: str) -> dict:
    if not raw.strip():
        return {}
    val = json.loads(raw)
    if not isinstance(val, dict):
        raise ValueError("arguments must be a JSON object like {\"key\": \"value\"}")
    return val


# ------------------------------------------------------------------ handlers
def cmd_start(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    tg.send_message(chat, HELP)


def cmd_video(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not arg:
        return tg.send_message(chat, "Usage: /video <idea>\nExample: /video 30 second Hindi motivational reel, calm lofi music")
    prefs = dict(memory.get_prefs(uid))
    prefs["notes"] = [n["text"] for n in memory.list_notes(uid, 10)]
    ok, msg = dispatch.dispatch_video(arg, chat, prefs)
    tg.send_message(chat, msg)


def _jpeg_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


def cmd_image(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not arg:
        return tg.send_message(chat, "Usage: /image <what to draw>")
    from agent.providers.image import generate_image
    status(tg, chat, "🖼 Generating the image…")
    prefs = memory.get_prefs(uid)
    size = {"16:9": (1280, 720), "1:1": (1024, 1024)}.get(prefs.get("aspect_ratio", ""), (1024, 1024))
    got = generate_image(arg, *size)
    if not got:
        return tg.send_message(chat, "❌ No image provider answered. Add NVIDIA_API_KEY for reliable images.")
    img, provider = got
    tg.send_photo(chat, _jpeg_bytes(img), caption=f"{shorten(arg, 200)}\n(via {provider})")


def cmd_ask(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not arg:
        return tg.send_message(chat, "Usage: /ask <question>")
    from agent.providers.llm import LLMUnavailable, complete
    try:
        answer = complete("You are a helpful, concise assistant. Reply in the language the user writes in "
                          "(Hindi, Hinglish or English).", arg, timeout=60)
    except LLMUnavailable:
        answer = ("❌ No LLM is reachable right now. Add NVIDIA_API_KEY (free at build.nvidia.com) "
                  "for dependable answers.")
    tg.send_message(chat, answer)


def cmd_research(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not arg:
        return tg.send_message(chat, "Usage: /research <topic>")
    from agent.tools.research import research_topic
    status(tg, chat, f"🔎 Searching the web: {shorten(arg, 60)}")
    res = research_topic(arg)
    text = f"{res['topic']}\n\n{res['summary']}"
    if res["sources"]:
        text += "\n\nSources:\n" + "\n".join(res["sources"])
    tg.send_message(chat, text)


def cmd_trends(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    from agent.tools.research import google_trends
    items = google_trends(arg.upper() or "IN", 10)
    if not items:
        return tg.send_message(chat, "❌ Could not load Google Trends right now.")
    tg.send_message(chat, "🔥 Trending now\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1))
                    + "\n\nTip: /video <one of these topics>")


# ---- skills
def _offer_approval(tg: Telegram, chat: int, res: sk.InstallResult) -> None:
    if not res.ok:
        return tg.send_message(chat, res.message)
    lines = [res.message, ""]
    s = sk.scan_skills().get(res.name)
    if s:
        lines += [f"📦 {s.name}: {s.description}",
                  f"Script: {s.script.name if s.script else 'none (instructions only)'}"]
    warns = res.warnings
    if warns:
        lines += ["", "⚠️ Things to check before approving:"] + [f"• {w.rule} ({w.file})" for w in warns[:8]]
    else:
        lines += ["", "No risky patterns found (the scan is only a hint - it cannot prove safety)."]
    lines += ["", "Approve only code you trust. It will run with your repo's runner."]
    tg.send_message(chat, "\n".join(lines),
                    reply_markup=kb([[("✅ Approve", f"skill:approve:{res.name}"),
                                      ("🗑 Reject", f"skill:reject:{res.name}")]]))


def cmd_addskill(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not arg:
        return tg.send_message(chat, "Usage: /addskill <github url>\nOr just send me a .zip / .md file.")
    status(tg, chat, "📦 Downloading and scanning the skill…")
    _offer_approval(tg, chat, sk.install_from_github(arg.split()[0]))


def cmd_updateskill(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    parts = arg.split()
    if not parts:
        return tg.send_message(chat, "Usage: /updateskill <name> [github url]")
    _offer_approval(tg, chat, sk.update_skill(parts[0], parts[1] if len(parts) > 1 else None))


def cmd_removeskill(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    tg.send_message(chat, sk.remove_skill(arg.strip()) if arg else "Usage: /removeskill <name>")


def cmd_skills(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    reg = sk.scan_skills()
    if not reg:
        return tg.send_message(chat, "No skills yet. Teach me one with /addskill <github url>.")
    tg.send_message(chat, "📚 Skills\n" + "\n".join(
        f"{'✅' if s.approved else '⏳'} {s.name} - {shorten(s.description, 70)}" for s in reg.values()))


def cmd_skill(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    tg.send_message(chat, sk.get_skill_detail(arg.strip()) if arg else "Usage: /skill <name>")


def cmd_runskill(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    name, _, raw = arg.partition(" ")
    if not name:
        return tg.send_message(chat, 'Usage: /runskill <name> {"key": "value"}')
    try:
        args = _json_arg(raw)
    except ValueError as e:
        return tg.send_message(chat, f"❌ Bad JSON: {e}")
    status(tg, chat, f"▶️ Running skill {name}…")
    res = sk.run_skill(name, args)
    if not res.get("ok"):
        return tg.send_message(chat, "❌ " + (res.get("error") or shorten(res.get("stderr", "failed"), 600)))
    if res.get("path"):
        sender = {"image": tg.send_photo}.get(res.get("kind", ""), None)
        if sender:
            return sender(chat, res["path"], caption=shorten(res.get("text", ""), 200))
        return tg.send_message(chat, f"✅ Done. Output kind '{res.get('kind')}' (saved on the runner).")
    tg.send_message(chat, "✅ " + (res.get("text") or shorten(res.get("stdout", ""), 800) or "done"))


def on_document(tg: Telegram, chat: int, uid: int, doc: dict) -> None:
    name = (doc.get("file_name") or "").lower()
    if not name.endswith((".zip", ".md")):
        return tg.send_message(chat, "Send a .zip (skill folder) or a SKILL.md file to teach me a skill.")
    try:
        data = tg.download_file(doc["file_id"])
    except TelegramError as e:
        return tg.send_message(chat, f"❌ Could not download the file: {e}")
    status(tg, chat, "📦 Scanning the skill…")
    if name.endswith(".zip"):
        res = sk.install_from_zip(data, doc.get("file_name", "skill.zip"))
    else:
        hint = name[:-3] if name != "skill.md" else "uploaded_skill"
        res = sk.install_from_markdown(data.decode("utf-8", "ignore"), hint, f"upload:{name}")
    _offer_approval(tg, chat, res)


# ---- connectors
def cmd_connectors(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    import connectors
    rows = connectors.status()
    tg.send_message(chat, "🔌 Connectors\n" + "\n".join(
        f"{'✅' if r['ready'] else '⚪'} {r['name']}" + ("" if r["ready"] else f"  (needs {', '.join(r['env'])})")
        for r in rows) + "\n\nUse: /connector <name> <tool> {json args}")


def cmd_connector(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    import connectors
    parts = arg.split(None, 2)
    if len(parts) < 2:
        return tg.send_message(chat, 'Usage: /connector <name> <tool> {"key": "value"}')
    try:
        out = connectors.call(parts[0], parts[1], **_json_arg(parts[2] if len(parts) > 2 else ""))
        tg.send_message(chat, shorten(json.dumps(out, ensure_ascii=False, indent=1, default=str), 3800))
    except Exception as e:  # noqa: BLE001
        tg.send_message(chat, f"❌ {type(e).__name__}: {shorten(str(e), 500)}")


# ---- jobs & settings
def cmd_status(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not dispatch.github_ready():
        return tg.send_message(chat, "Status needs GITHUB_REPO and GITHUB_TOKEN (set automatically on Actions).")
    try:
        runs = dispatch.list_runs(chat)
    except Exception as e:  # noqa: BLE001
        return tg.send_message(chat, f"❌ Could not read jobs: {type(e).__name__}")
    if not runs:
        return tg.send_message(chat, "No video jobs yet.")
    icon = {"completed": "✅", "in_progress": "⏳", "queued": "🕒"}
    tg.send_message(chat, "🎞 Recent jobs\n" + "\n".join(
        f"{icon.get(r['status'], '•')} #{r['id']} {r['conclusion'] or r['status']} - {r['title']}" for r in runs)
        + "\n\nCancel one with /cancel <number>")


def cmd_cancel(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not arg.strip().isdigit():
        return tg.send_message(chat, "Usage: /cancel <run id> (see /status)")
    tg.send_message(chat, dispatch.cancel_run(int(arg.strip()), chat))


def cmd_settings(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    p = memory.get_prefs(uid)
    shown = {k: v for k, v in p.items() if k != "feedback"}
    tg.send_message(chat, f"⚙️ Settings\n{json.dumps(shown, ensure_ascii=False) if shown else '(defaults)'}",
                    reply_markup=kb([
                        [("हिंदी voice (Swara)", "pref:voice:hi-IN-SwaraNeural"),
                         ("हिंदी voice (Madhur)", "pref:voice:hi-IN-MadhurNeural")],
                        [("English (Aria)", "pref:voice:en-US-AriaNeural"),
                         ("English (Guy)", "pref:voice:en-US-GuyNeural")],
                        [("Lang: हिंदी", "pref:language:hi"), ("Lang: English", "pref:language:en"),
                         ("Hinglish", "pref:language:hinglish")],
                        [("9:16", "pref:aspect_ratio:9:16"), ("16:9", "pref:aspect_ratio:16:9"),
                         ("1:1", "pref:aspect_ratio:1:1")],
                        [("Music ON", "pref:music:true"), ("Music OFF", "pref:music:false")],
                        [("Captions ON", "pref:captions:true"), ("Captions OFF", "pref:captions:false")],
                    ]))


def cmd_watermark(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not arg:
        return tg.send_message(chat, "Usage: /watermark @yourhandle   (or /watermark off)")
    memory.set_pref(uid, "watermark", None if arg.lower() == "off" else arg[:40])
    tg.send_message(chat, "✅ Watermark " + ("removed." if arg.lower() == "off" else f"set to {arg[:40]}"))


def cmd_feedback(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not arg:
        return tg.send_message(chat, 'Usage: /feedback shorter videos, more energetic')
    notes = memory.add_feedback(uid, arg)
    tg.send_message(chat, f"📝 Noted. I apply your last {len(notes)} notes to the next videos.")


def cmd_remember(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not arg:
        return tg.send_message(chat, 'Usage: /remember <fact>\nExample: /remember my channel is @xyz and I like short punchy lines')
    nid = memory.add_note(uid, arg)
    where = "permanently (Cloudflare D1)" if d1.enabled() else "(saved in the repo)"
    tg.send_message(chat, f"🧠 Remembered #{nid} {where}. I will apply it to every video.")


def cmd_notes(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    notes = memory.list_notes(uid)
    tg.send_message(chat, "🧠 I remember:\n" + "\n".join(f"#{n['id']} {n['text']}" for n in notes)
                    + "\n\nRemove one: /forget <id>" if notes else "Nothing yet. Add one with /remember <fact>.")


def cmd_forget(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not arg.strip().lstrip("#").isdigit():
        return tg.send_message(chat, "Usage: /forget <id>   (see /notes)")
    ok = memory.delete_note(uid, int(arg.strip().lstrip("#")))
    tg.send_message(chat, "🗑 Forgotten." if ok else "❌ No such note.")


def cmd_storage(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    if not d1.enabled():
        return tg.send_message(chat, "💾 Memory: JSON files in the repo (works, but D1 is sturdier).\n"
                               "Add CLOUDFLARE_ACCOUNT_ID, D1_DATABASE_ID and CLOUDFLARE_API_TOKEN to use Cloudflare D1.")
    try:
        counts = d1.status()
    except d1.D1Error as e:
        return tg.send_message(chat, f"❌ D1 is configured but not reachable: {e}")
    tg.send_message(chat, "💾 Cloudflare D1 connected ✅\n" + "\n".join(f"{k}: {v}" for k, v in counts.items()))


def cmd_providers(tg: Telegram, chat: int, uid: int, arg: str) -> None:
    tg.send_message(chat, "🔧 Providers\n" + "\n".join(
        f"{'✅' if on else '⚪'} {name}" for name, on in enabled_providers().items()))


# Commands that can take a while: they run inside the "Thinking…" animation (bot/thinking.py).
SLOW = {"ask": "typing", "research": "typing", "trends": "typing", "image": "upload_photo",
        "addskill": "typing", "updateskill": "typing", "runskill": "typing", "connector": "typing"}

COMMANDS = {
    "start": cmd_start, "help": cmd_start, "video": cmd_video, "image": cmd_image, "ask": cmd_ask,
    "research": cmd_research, "trends": cmd_trends, "addskill": cmd_addskill, "updateskill": cmd_updateskill,
    "removeskill": cmd_removeskill, "skills": cmd_skills, "skill": cmd_skill, "runskill": cmd_runskill,
    "connectors": cmd_connectors, "connector": cmd_connector, "status": cmd_status, "cancel": cmd_cancel,
    "settings": cmd_settings, "watermark": cmd_watermark, "feedback": cmd_feedback, "providers": cmd_providers,
    "remember": cmd_remember, "notes": cmd_notes, "forget": cmd_forget, "storage": cmd_storage,
}


def on_text(tg: Telegram, chat: int, uid: int, text: str) -> None:
    r = router.route(text)
    intent = r["intent"] if r["intent"] in ("video", "image", "research", "trends") else "ask"
    handler = {"video": cmd_video, "image": cmd_image, "research": cmd_research,
               "trends": cmd_trends, "ask": cmd_ask}[intent]
    if intent == "video":                                   # only queues a job: instant, no animation
        return handler(tg, chat, uid, r["payload"])
    with Thinking(tg, chat, action=SLOW[intent]):
        handler(tg, chat, uid, r["payload"] if intent != "trends" else "")


# ------------------------------------------------------------------ callbacks
def on_callback(tg: Telegram, cq: dict) -> None:
    uid = cq["from"]["id"]
    msg = cq.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    if not _authorized(uid) or chat is None:
        return tg.answer_callback(cq["id"], "Not allowed")
    parts = (cq.get("data") or "").split(":")
    kind = parts[0]
    if kind == "pref" and len(parts) >= 3:
        key, val = parts[1], ":".join(parts[2:])
        if key not in ("voice", "language", "aspect_ratio", "music", "captions"):
            return tg.answer_callback(cq["id"], "Unknown setting")
        if key in ("music", "captions"):
            val = val == "true"
        memory.set_pref(uid, key, val)
        tg.answer_callback(cq["id"], f"{key} = {val}")
        return tg.edit_message_text(chat, msg["message_id"], f"✅ {key} = {val}")
    if kind == "skill" and len(parts) == 3 and parts[1] in ("approve", "reject"):
        text = sk.approve_skill(parts[2]) if parts[1] == "approve" else sk.reject_skill(parts[2])
        tg.answer_callback(cq["id"], "Done")
        return tg.edit_message_text(chat, msg["message_id"], text)
    tg.answer_callback(cq["id"], "?")


# --------------------------------------------------------------------- entry
def _sync_skills() -> None:
    """With Cloudflare D1, make the local skills folder match the database before doing anything."""
    if d1.enabled():
        try:
            sk.store.sync_down()
        except Exception as e:  # noqa: BLE001
            log.warning("skill sync from D1 failed: %s", e)


def handle_update(update: dict, tg: Telegram) -> None:
    _sync_skills()
    if "callback_query" in update:
        return on_callback(tg, update["callback_query"])
    msg = update.get("message")
    if not msg or not msg.get("from"):
        return
    chat, uid = msg["chat"]["id"], msg["from"]["id"]
    if not _authorized(uid):
        return tg.send_message(chat, f"⛔ Not authorised.\nYour Telegram ID is {uid}. "
                               f"The owner must add it to the ALLOWED_USER_IDS secret.")
    try:
        if msg.get("document"):
            return on_document(tg, chat, uid, msg["document"])
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text.startswith("/"):
            cmd, arg = _args(text)
            fn = COMMANDS.get(cmd)
            if not fn:
                return tg.send_message(chat, "Unknown command. Try /help")
            if cmd in SLOW:
                with Thinking(tg, chat, action=SLOW[cmd]):
                    return fn(tg, chat, uid, arg)
            return fn(tg, chat, uid, arg)
        on_text(tg, chat, uid, text)
    except TelegramError:
        raise
    except Exception as e:  # noqa: BLE001 - one bad message must never stop the bot
        log.exception("handler failed")
        tg.send_message(chat, f"⚠️ Something went wrong: {type(e).__name__}: {shorten(str(e), 300)}")
