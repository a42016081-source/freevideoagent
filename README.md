# 🎬 FreeVideoAgent

Apna khud ka AI video agent. Telegram par likho *"30 second ka Hindi motivational reel, calm music"* aur video bankar wapas aa jata hai. **GitHub Actions par free chalta hai**, koi server nahi chahiye.

- Script, voice (edge-tts), pictures, music, captions, thumbnail, hashtags: sab code se, **bina kisi paid API ke**
- Optional: NVIDIA API (behtar script + AI images), ElevenLabs (better voice), Pexels (stock footage)
- **Skills**: GitHub link, `.zip` ya `.md` bhejo, agent naya kaam seekh leta hai (aapke approve karne ke baad)
- **Connectors**: GitHub, MCP server, koi bhi REST/webhook
- **Always-on bot**: ek GitHub Actions job 5h50m chalta hai aur khud dobara start ho jata hai (har 6 ghante restart). Jawab 1-2 second me.
- **Web chat**: browser me ID + password se login, Telegram jaisa sab kuch (`web/README.md`)
- **"Thinking…" animation**: Telegram aur web dono par (jaise Claude me), video ke steps ke saath

## Kya kya hai

| Feature | Kaise |
|---|---|
| Plan / script (viral hook, loop ending) | LLM: NVIDIA → OpenAI-compatible → Pollinations (free) → Ollama → template (kabhi crash nahi) |
| Voice | edge-tts (free, Hindi/English) → ElevenLabs (optional) → espeak (offline) |
| Visuals | Pexels video (key ho to) → NVIDIA/Pollinations AI image → Pixabay → generated gradient |
| Music + SFX | Khud synthesize hota hai (copyright-free), voice ke neeche auto-duck. Apne tracks `assets/music/` me daal sakte ho |
| Captions | Karaoke style (bolte shabd par highlight), Hindi + English, libass se (Devanagari sahi dikhta hai) |
| Render | Seedha ffmpeg (MoviePy nahi): 9:16 / 16:9 / 1:1, Ken Burns zoom, fade cuts |
| Extra | Thumbnail, A/B titles, hashtags, retention checklist, best posting time |

## Setup (10 steps)

1. **Telegram bot**: Telegram me `@BotFather` → `/newbot` → token copy karo.
2. **Apna Telegram ID**: `@userinfobot` ko message karo → number copy karo.
3. **Repo**: GitHub par is project ko apne account me upload / fork karo. **Public** rakho: Always-on bot 24 ghante chalta hai, aur Actions sirf public repo me unlimited free hai (private me 2000 min/month). Public se sirf *code* dikhta hai, **Secrets kabhi public nahi hote**. Notes/settings private rakhne ke liye D1 lagao (neeche).
4. **Secrets**: repo → *Settings → Secrets and variables → Actions → New repository secret*:

   | Secret | Zaruri? | Kya |
   |---|---|---|
   | `TELEGRAM_BOT_TOKEN` | ✅ | BotFather wala token |
   | `ALLOWED_USER_IDS` | ✅ | Aapka ID (kai ho to comma se: `123,456`). **Khali = koi allowed nahi** |
   | `NVIDIA_API_KEY` | optional | neeche dekho |
   | `OPENAI_BASE_URL`, `OPENAI_API_KEY` | optional | koi bhi OpenAI-compatible (OpenRouter, Groq…) |
   | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | optional | behtar voice |
   | `PEXELS_API_KEY`, `PIXABAY_API_KEY` | optional | stock footage / photos |
   | `CLOUDFLARE_ACCOUNT_ID`, `D1_DATABASE_ID`, `CLOUDFLARE_API_TOKEN` | optional | permanent memory (D1), neeche section dekho |

5. **Actions permission**: *Settings → Actions → General → Workflow permissions* → **Read and write permissions** → Save. (Bot ko apni settings/skills repo me save karni hoti hain.)
6. **Actions ON**: *Actions* tab kholo → agar bole "enable" to enable karo.
7. **Bot jagao**: *Actions → "Bot (always on, restarts every 6 hours)" → Run workflow*. Ye 5h50m chalta hai, phir khud dobara start hota hai; cron (`0 */6 * * *`) safety net hai.
8. **Test**: Telegram me bot ko `/start` bhejo, phir `/video 20 second ka calm english reel`.
9. Jawab **1-2 second** me aata hai (video banne me 5-15 min lagte hain, us dauran "Thinking…" animation chalta hai).
10. Video ban kar Telegram me aa jayega. Download bhi kar sakte ho: *Actions → run → Artifacts*.

> Bot ko pehli baar message karne par agar "Not authorised, your ID is …" aaye, to wo ID `ALLOWED_USER_IDS` me daal do.

## Always-on bot (har 6 ghante restart)

`bot_loop.yml` ek hi job me bot ko ~5h50m chalata hai (`python -m bot.loop`): Telegram ko long-polling se sunta hai (jawab 1-2 sec me), web chat ko bhi, aur har message alag thread me handle hota hai (`/research` jaisa slow kaam agle message ko nahi rokta). Time poora hone par job `bot_loop.yml` ko dobara start kar deta hai. Agar ye hand-over fail ho jaye to cron (har 360 min) bot ko wapas chala deta hai. Beech ke kuch second ke messages kho nahi jate: Telegram 24 ghante tak rakhta hai, web inbox D1 me hai.

- **Public repo chahiye** (24 ghante = ~1440 Actions minute/din, sirf public me free).
- **D1 zaroor lagao.** Bina D1 ke notes/settings repo me commit hote hain, aur public repo me wo sabko dikhenge. Keys/tokens phir bhi safe hain (wo Secrets me hain).
- `bot_poll.yml` ab sirf haath se chalta hai (do jagah se `getUpdates` padhna ek doosre ko tod deta hai). Loop shuru hote hi purana webhook (Instant mode) hata deta hai.
- Telegram ya web, jo chaho wo chalao: `TELEGRAM_BOT_TOKEN` ho to Telegram, `WEB_BASE_URL` + `BRIDGE_SECRET` ho to web.
- Dhyan: `/runskill` is job me chalta hai jahan `GITHUB_TOKEN` (write) aur baaki secrets hote hain. Approved skill ko ye dikh sakte hain, isliye **sirf padhi hui skill approve karo**.

## Web chat (browser me)

Cloudflare Worker + D1 + KV par, free. Setup: **`web/README.md`**. Login ID/password aap Cloudflare secrets me rakhte ho (`WEB_USER`, `WEB_PASSWORD`, `WEB_UID`), aur GitHub me `WEB_BASE_URL` + `BRIDGE_SECRET` daalte ho.

## "Thinking…" animation

- **Telegram**: ek message jo har ~1.5 sec edit hota hai (`✻ Thinking… (7s)` + abhi kya ho raha hai), saath me "typing…" dikhta hai. Kaam khatam hote hi message hat jata hai. Video job me wahi message steps dikhata hai (script, voice, visuals…) aur aakhir me "✅ Video ready!" ban jata hai.
- **Web**: spark icon ghoomta hai, "Thinking…" shimmer karta hai, timer chalta hai, steps ek ek karke aate hain. Khatam hone par "Thought for 12s" ban jata hai aur click karke steps khul jate hain.
- Telegram ki edit limit ke liye: lambe jobs me edits dheere ho jate hain, aur "Too Many Requests" aane par bot khud ruk jata hai.

## NVIDIA API kaise lagayein

1. <https://build.nvidia.com> → login → koi bhi model kholo → **Get API Key** → `nvapi-...` copy karo (free credits milte hain).
2. Repo me secret banao: **`NVIDIA_API_KEY`** = wo key. Bas. Code apne aap use karne lagta hai.
3. Isse: behtar script (Llama 3.3 70B) + AI images (FLUX.1-schnell), `/ask`, `/research` bhi tez aur reliable.
4. Model badalna ho to repo ke *Variables* (Secrets ke bagal wala tab) me: `NVIDIA_MODEL` (jaise `meta/llama-3.3-70b-instruct`) aur `NVIDIA_IMAGE_MODEL` (jaise `black-forest-labs/flux.1-schnell` ya `stabilityai/stable-diffusion-3-medium`).
5. Check: Telegram me `/providers` → `nvidia_llm ✅`.

> NVIDIA ke model naam / rate limits kabhi bhi badal sakte hain. Agar image ya text na aaye to Actions log me "NVIDIA ... failed" dekho aur `NVIDIA_MODEL` / `NVIDIA_IMAGE_MODEL` variable badlo. Fail hone par agent apne aap free Pollinations par chala jata hai.

## Commands

```
/video <idea>        video banao (ya seedha likh do)
/image <prompt>      image
/ask <sawal>         chat / code / writing
/research <topic>    web research + sources
/trends              aaj India me kya trend kar raha hai
/addskill <github>   skill seekho (repo / folder / SKILL.md)   |  .zip ya .md file bhejo
/skills  /skill <n>  /runskill <n> {json}  /updateskill <n>  /removeskill <n>
/connectors  /connector <name> <tool> {json}
/remember <baat>  /notes  /forget <id>  /storage
/settings  /watermark <text|off>  /feedback <text>  /providers
/status  /cancel <run id>
```

## Skill kaise sikhayein

Skill = ek folder jisme `SKILL.md` hota hai (aur optional `script.py`, `requirements.txt`). Example: `skills/example_quote_video/`.

1. `/addskill https://github.com/owner/repo` (ya folder link `.../tree/main/folder`, ya `SKILL.md` ka link). Ya Telegram me seedha `.zip` / `.md` file bhejo.
2. Bot download + scan karke batata hai ki kya risky laga aur **✅ Approve / 🗑 Reject** button deta hai.
3. Approve karne ke baad hi skill use hoti hai: uske instructions planner ko milte hain, aur agar script hai to wo kisi scene ka image/video bana sakti hai (`/runskill` se test bhi kar sakte ho).
4. Agar repo me SKILL.md nahi hai, README se wrapper bana diya jata hai (sirf instructions).
5. Ek repo me kai skills hon to ek folder ka link do.

**⚠️ Safety (imaandari se):** install ke time kuch execute nahi hota. Scan sirf hint hai, sandbox nahi. Approve karne ke baad script aapke runner par chalti hai (saaf environment, timeout, sirf output folder ka file allowed), lekin ek malicious script jise aapne approve kiya, nuksan kar sakti hai. **Sirf wahi skill approve karo jo aapne padhi ho.**

## Connector kaise jodein

`connectors/` me ek file daalo jisme `NAME`, `ENV_VARS`, `connect()`, `list_tools()`, `call()` ho (dekho `connectors/webhook_connector.py`). Apne aap detect hota hai. Built-in: `github`, `mcp` (HTTP MCP servers; env `MCP_SERVER_URL`, `MCP_API_KEY`), `webhook` (env `WEBHOOK_URL`). Telegram me `/connectors`.

## ElevenLabs voice

Secrets me `ELEVENLABS_API_KEY` (aur `ELEVENLABS_VOICE_ID`) daalo. Voice ab ElevenLabs se banegi; fail ho to edge-tts par wapas.

## Cloudflare D1 (permanent yaaddasht)

GitHub ka runner har baar naya hota hai, isliye bina database ke settings/skills repo me commit karke bachani padti hain. **D1 lagane ke baad sab kuch database me rehta hai** aur restart ke baad bhi yaad rehta hai: aapki settings, `/remember` wali baatein, job history, aur **installed skills (approval ke saath)**.

1. Cloudflare dashboard → **Storage & Databases → D1 → Create database** (naam: `freevideoagent`). Database ka **ID** copy karo.
   (Ya terminal se: `npx wrangler d1 create freevideoagent`)
2. **Account ID**: dashboard ke right side me / URL me dikhta hai.
3. **API token**: *My Profile → API Tokens → Create Token → Custom token* → Permission: **Account → D1 → Edit** (kuch aur nahi) → token copy karo.
4. GitHub Secrets me teen secret banao: `CLOUDFLARE_ACCOUNT_ID`, `D1_DATABASE_ID`, `CLOUDFLARE_API_TOKEN`.
5. Tables apne aap ban jate hain (pehli baar use par). Check: Telegram me `/storage` → "Cloudflare D1 connected ✅".
6. Pehle se jo local settings/skills hain unhe D1 me daalna ho (sirf ek baar): apne computer par `.env` me teeno values daalo aur `python -m agent.d1 migrate` chalao.

Naye commands: `/remember <baat>` (jaise "mera channel @xyz hai, mujhe chhote punchy lines pasand hain"), `/notes`, `/forget <id>`, `/storage`. Remembered baatein har video ke planner ko mil jati hain.

**Dhyan rakho**
- D1 lagane ke baad **D1 hi sach hai**: repo ke `skills/` folder me jo purani skills committed hain (example ke alawa), wo D1 me na hon to run ke time hata di jati hain. Pehle `migrate` karo.
- Ek skill packed size me max ~3 MB ho sakti hai (D1 ki limit ke wajah se).
- Token sirf D1 Edit permission wala banao. Wo video job ke environment me hota hai jahan approved skills chalti hain, isliye chhota permission rakhna zaroori hai.
- D1 ka free plan chhote bot ke liye kaafi hai (limits Cloudflare badalta rehta hai, dashboard me dekh lo).

## Instant mode (sirf private repo ke liye)

**Always-on bot (`bot_loop.yml`) hone par iski zaroorat nahi.** Ye tab kaam ka hai jab repo private ho (tab loop free nahi hai). Cloudflare Worker (free) relay:

1. `bot_poll.yml` ko disable karo (Actions tab → workflow → ⋯ → Disable). Webhook aur polling saath nahi chalte.
2. GitHub me fine-grained token banao: sirf is repo ke liye, permission **Contents: Read and write**.
3. `cd bot/worker && npx wrangler login && npx wrangler deploy`
4. Secrets: `npx wrangler secret put GITHUB_TOKEN` (upar wala), `GITHUB_REPO` (`owner/repo`), `WEBHOOK_SECRET` (koi lamba random text), `ALLOWED_USER_IDS`.
5. Telegram ko batao: browser me kholo
   `https://api.telegram.org/bot<TOKEN>/setWebhook?url=<worker-url>&secret_token=<WEBHOOK_SECRET>`
6. Ab har message turant `bot_update.yml` chalata hai. Wapas cron par jana ho to `.../deleteWebhook` kholo aur `bot_poll.yml` enable karo.

## Apne computer par chalana

```bash
pip install -r requirements.txt        # + ffmpeg install hona chahiye
cp .env.example .env                    # token waghera bharo
python scripts/dry_run.py               # bina internet ke test video
python -m bot.local_run                 # bot (videos local me render honge)
python -m pytest                        # tests
```

## Honest limits

- **Speed**: GitHub CPU par 30-60 sec ka video ~5-15 min leta hai.
- **Restart gap**: har 6 ghante bot kuch second ke liye restart hota hai. Is dauran bheje messages restart ke baad handle hote hain.
- **GitHub ki policy**: 24x7 chalne wala bot Actions ka intended use nahi hai; GitHub rok sakta hai. Agar GitHub aapatti kare to private repo + Instant mode par chale jao.
- **60 din**: public repo me 60 din koi activity na ho to GitHub scheduled workflows band kar deta hai (Actions tab me phir enable karo).
- **Telegram 50 MB**: bada video GitHub Release link ke roop me aata hai.
- **Music**: built-in music simple synthesized background hai (copyright-free), Spotify jaisa nahi. Asli music ke liye CC0 tracks `assets/music/` me daalo.
- **Bina key ke quality**: free LLM/image endpoints kabhi slow ya down hote hain. Tab agent template script aur gradient visuals par girta hai (video phir bhi banta hai, par basic). **NVIDIA key lagane se sabse bada farq padta hai.**
- **Fixed versions nahi**: `requirements.txt` me jaan-boojhkar `>=` hai, kyunki edge-tts/ddgs jaise scraping libraries purane version par toot jati hain.
- **Copyright / policy**: voice/music/stock ke license aur platform (YouTube/Instagram) ke rules aapki zimmedari hain. GitHub Actions ko content-farming ya heavy abuse ke liye use karna unki terms ke khilaf ho sakta hai.

## Kya test hua, kya nahi

✅ Tested (automatic tests): planner, router, captions (Hindi bhi, frame dekh kar), audio mix/ducking, music, skills (install/scan/zip-slip/approval/safe run), bot commands, connectors (MCP protocol fake server par), NVIDIA request format (fake server par), D1 memory (D1 ke REST API ke fake par, asli SQLite ke saath: restart ke baad skills/prefs/notes wapas aana, remove/update, tampering), asli ffmpeg render (offline), YAML/JS syntax; **naya**: thinking animation (Telegram edits + web events), always-on loop (offsets, deadline, restart, crash-safety), web bridge, aur asli `web/worker.js` Node par fake D1/KV ke saath (login, rate limit, CSRF, files, poora loop), UI Chromium me.

⚠️ Sirf aapke repo par pehli baar chalakar pata chalega (maine live internet / Telegram / GitHub se test nahi kiya; **asli Cloudflare D1/KV par web chat bhi nahi**, `/healthz` aur login pehle check kar lena; Telegram me animation ki edit-rate limit asli chat me dekhni hogi; 6 ghante ka self-restart asli Actions par): edge-tts voice, Pollinations/NVIDIA ke asli jawab, Telegram se asli messages, asli Cloudflare D1 (API ka format maine docs ke hisaab se likha hai), Actions ka cron/commit/dispatch flow, Cloudflare Worker, Ollama install step. Pehle `ci.yml` (Tests workflow) hara hona chahiye, phir `/video`.

## Folder map

```
agent/        planner, providers (LLM/image), tools (tts, visuals, captions, music, video_engine, ...), skills/, run_job.py
bot/          Telegram client, commands, thinking.py (animation), loop.py (always-on), web.py (web bridge), poll / process_update / local_run, worker/ (Cloudflare relay)
web/          web chat: worker.js (deploy), src/, test/, README.md
connectors/   github, mcp, webhook
skills/       example_quote_video/
.github/workflows/  bot_loop.yml (always-on)  video.yml  bot_poll.yml (manual)  bot_update.yml  install_skill.yml  ci.yml
tests/  scripts/  assets/  state/  config.yaml  .env.example
```

License: MIT.
