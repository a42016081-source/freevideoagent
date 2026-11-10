# Web chat (browser me bot se baat karo)

Browser kholo → **ID + password** se login → Telegram jaisa hi sab kuch (`/video`, `/image`, `/ask`, `/research`, buttons, video player) aur wahi **"Thinking…" animation**.
Sab **free** hai: Cloudflare Worker + D1 + KV (free plan) aur GitHub Actions (public repo).

```
Browser  ⇄  Cloudflare Worker (login, chat, files)  ⇄  D1  ⇄  Bot (GitHub Actions, bot_loop.yml)
```
Bot ka koi public address nahi hota, isliye bot khud Worker se message **kheenchta** hai (long-poll) aur jawab/video wahan **bhejta** hai.

## Setup (10 minute)

Do raaste hain. Terminal pasand ho to **A**, nahi to **B**.

### A. Terminal se
```bash
cd web
npx wrangler login
npx wrangler kv namespace create FILES          # jo "id" aaye use wrangler.toml me daalo
# D1 pehle se banaya hai (bot ki memory ke liye) to wahi use karo, warna:
npx wrangler d1 create freevideoagent           # "database_id" wrangler.toml me daalo

# 5 secrets (har ek par value paste karna hota hai):
python -c "import secrets;print(secrets.token_urlsafe(32))"     # SESSION_SECRET aur BRIDGE_SECRET ke liye 2 baar chalao
npx wrangler secret put SESSION_SECRET          # lamba random text
npx wrangler secret put BRIDGE_SECRET           # dusra lamba random text (GitHub me bhi yahi jayega)
npx wrangler secret put WEB_USER                # aapka login ID, jaise: jon
npx wrangler secret put WEB_PASSWORD            # aapka password (lamba rakho)
npx wrangler secret put WEB_UID                 # aapka Telegram ID (wahi jo ALLOWED_USER_IDS me hai)
npx wrangler deploy                             # URL milega: https://freevideoagent-web.<naam>.workers.dev
```

### B. Sirf dashboard se (terminal nahi)
1. Cloudflare dashboard → **Storage & Databases → KV → Create namespace** (naam `FILES`). D1 database (`freevideoagent`) bana lo agar nahi hai.
2. **Workers & Pages → Create → Create Worker** → Deploy → **Edit code** → saari purani cheez hata kar **`web/worker.js` ka poora text paste** karo → Deploy.
3. Worker → **Settings → Bindings → Add**: D1 (variable name **`DB`**) aur KV (variable name **`FILES`**).
4. Worker → **Settings → Variables and Secrets → Add** (type **Secret**): `SESSION_SECRET`, `BRIDGE_SECRET`, `WEB_USER`, `WEB_PASSWORD`, `WEB_UID` (upar jaisa). Deploy.

### Dono ke baad: GitHub Secrets (repo → Settings → Secrets → Actions)
| Secret | Value |
|---|---|
| `WEB_BASE_URL` | Worker ka URL, bina aakhri `/` ke: `https://freevideoagent-web.xxx.workers.dev` |
| `BRIDGE_SECRET` | **wahi** jo Worker me daala |

Phir *Actions → "Bot (always on…)" → Run workflow* (ya agle 6-ghante ke cron ka intezaar). Worker ka URL browser me kholo, login karo, `/help` bhejo.

## Zaroori baatein
- **`WEB_UID` = aapka Telegram ID** aur wo `ALLOWED_USER_IDS` secret me hona chahiye, warna bot bolega "Not authorised". Isi ID se web aur Telegram ki settings/notes/skills ek hi rehti hain.
- Kai log chahiye to ek secret `WEB_USERS` banao: `{"jon":{"password":"...","uid":111},"asha":{"password":"...","uid":222}}` (har `uid` `ALLOWED_USER_IDS` me hona chahiye).
- Password Cloudflare ke secret store me rehta hai (sirf aapko dikhta hai, GitHub/repo me kuch nahi). Login par 8 galat koshish ke baad us IP ko 10 min ke liye rok diya jata hai. Cookie `HttpOnly + Secure + SameSite=Strict`, 7 din chalti hai.
- Web par `.zip` / `.md` file upload nahi hota. Skill ke liye `/addskill <github link>` use karo (file wala tareeka Telegram par hai).
- Video/image 7 din tak chat me rehte hain (KV limit: ek file max 24 MB; bot web ke liye video 24 MB ke andar hi banata hai).
- Free limits (Cloudflare kabhi badal sakta hai): Worker 100,000 requests/din, KV 1,000 writes/din (har video + thumbnail = 2 writes), D1 ka free plan. Ek bot + ek user ke liye kaafi.

## Files
- `worker.js` — wo ek file jo deploy hoti hai (**generated**).
- `src/worker.src.js` + `src/index.html` — asli source. Badalne ke baad: `python web/build.py`.
- `test/harness.mjs` — Worker ko local chalata hai (fake D1/KV, Node 22+): `node web/test/harness.mjs 8787`, phir <http://127.0.0.1:8787>.

## Kya test hua
Asli `worker.js` Node par fake D1/KV ke saath chali: login, galat password, rate limit, CSRF, message → bot → jawab, buttons, long-poll, file upload/Range, aur poora bot loop (17 tests). UI Chromium me dekhi (mobile + desktop). **Asli Cloudflare D1/KV par test nahi hua**: pehli baar deploy par `/healthz` aur login check kar lena.
