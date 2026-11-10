// FreeVideoAgent web chat - Cloudflare Worker (free plan).
//
//   Browser  <->  this Worker  <->  D1 (messages + events)  <->  the bot (GitHub Actions loop, bot/loop.py)
//
// The bot has no public address, so it PULLS: it long-polls /bridge/next for messages you typed on
// the web, and PUSHES answers / "thinking" steps / videos to /bridge/event and /bridge/file.
// The browser only talks to /api/* (login cookie). /bridge/* needs the BRIDGE_SECRET.
//
// Bindings:  DB (D1, the same database as the bot's memory)   FILES (KV namespace, for videos/images)
// Secrets:   SESSION_SECRET  BRIDGE_SECRET  WEB_USER  WEB_PASSWORD  WEB_UID
//            (WEB_UID = your Telegram user id, so the web chat shares your bot memory/permissions;
//             optional WEB_USERS = JSON {"name": {"password": "...", "uid": 123}} for more people)
//
// This file is generated: edit web/src/worker.src.js + web/src/index.html, then run `python web/build.py`.

const INDEX_HTML = "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n<meta name=\"color-scheme\" content=\"light dark\">\n<meta name=\"robots\" content=\"noindex\">\n<title>FreeVideoAgent</title>\n<style>\n:root{\n  --bg:#f7f5f1; --panel:#ffffff; --fg:#1f1d1a; --muted:#8a847b; --line:#e6e1d8;\n  --accent:#d9623b; --accent-fg:#ffffff; --user:#ece7dd; --code:#f0ece4; --ok:#2f8f5b; --err:#c0392b;\n  --shadow:0 1px 2px rgba(30,25,15,.06),0 8px 24px rgba(30,25,15,.06);\n}\n@media (prefers-color-scheme: dark){\n  :root{ --bg:#171614; --panel:#201e1b; --fg:#ece8e1; --muted:#9a948a; --line:#34312c;\n         --accent:#e8774f; --accent-fg:#1a1410; --user:#2b2823; --code:#2a2723;\n         --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35); }\n}\n*{box-sizing:border-box}\nhtml,body{height:100%}\nbody{margin:0;background:var(--bg);color:var(--fg);\n  font:16px/1.55 ui-sans-serif,system-ui,-apple-system,\"Segoe UI\",Roboto,\"Noto Sans\",\"Noto Sans Devanagari\",sans-serif;\n  -webkit-text-size-adjust:100%}\n[hidden]{display:none!important}\nbutton,input,textarea{font:inherit;color:inherit}\nbutton{cursor:pointer}\n\n/* ---------- login ---------- */\n#login{min-height:100dvh;display:grid;place-items:center;padding:24px 16px}\n.card{width:100%;max-width:380px;background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:28px 24px;box-shadow:var(--shadow)}\n.brand{display:flex;align-items:center;gap:10px;font-weight:650;font-size:20px;margin:0 0 4px}\n.brand svg{width:24px;height:24px;color:var(--accent)}\n.sub{margin:0 0 22px;color:var(--muted);font-size:14px}\nlabel{display:block;font-size:13px;color:var(--muted);margin:14px 0 6px}\n.field{width:100%;padding:11px 13px;border:1px solid var(--line);border-radius:11px;background:transparent;outline:none}\n.field:focus{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent)}\n.pw{position:relative}\n.pw button{position:absolute;right:6px;top:50%;transform:translateY(-50%);background:none;border:0;color:var(--muted);padding:6px 8px;font-size:13px}\n.primary{width:100%;margin-top:20px;padding:12px;border:0;border-radius:11px;background:var(--accent);color:var(--accent-fg);font-weight:600}\n.primary:disabled{opacity:.55;cursor:default}\n.err{color:var(--err);font-size:14px;min-height:20px;margin:12px 0 0}\n\n/* ---------- app shell ---------- */\n#app{height:100dvh;display:flex;flex-direction:column;max-width:860px;margin:0 auto}\nheader{display:flex;align-items:center;gap:10px;padding:12px 16px;padding-top:max(12px,env(safe-area-inset-top));border-bottom:1px solid var(--line)}\nheader .brand{font-size:17px;margin:0;flex:1}\nheader .brand svg{width:20px;height:20px}\n.who{font-size:13px;color:var(--muted)}\n.ghost{background:none;border:1px solid var(--line);border-radius:9px;padding:5px 10px;font-size:13px;color:var(--muted)}\n.ghost:hover{color:var(--fg);border-color:var(--muted)}\n#log{flex:1;overflow-y:auto;padding:18px 16px 8px;scroll-behavior:smooth}\n.row{display:flex;margin:0 0 14px}\n.row.me{justify-content:flex-end}\n.bubble{max-width:min(88%,640px);padding:10px 14px;border-radius:16px;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}\n.me .bubble{background:var(--user);border-bottom-right-radius:5px}\n.bot .bubble{background:var(--panel);border:1px solid var(--line);border-bottom-left-radius:5px}\n.bubble a{color:var(--accent)}\n.bubble code{background:var(--code);padding:1px 5px;border-radius:5px;font:13.5px ui-monospace,SFMono-Regular,Menlo,monospace}\n.bubble pre{background:var(--code);padding:10px 12px;border-radius:10px;overflow-x:auto;margin:8px 0;white-space:pre}\n.bubble pre code{background:none;padding:0}\n.btns{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;white-space:normal}\n.chip{border:1px solid var(--line);background:var(--bg);border-radius:999px;padding:6px 13px;font-size:14px}\n.chip:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}\n.chip:disabled{opacity:.5;cursor:default}\n.media{max-width:min(88%,420px)}\n.media img,.media video{display:block;width:100%;border-radius:14px;border:1px solid var(--line);background:#000}\n.media .cap{font-size:14px;color:var(--muted);margin-top:6px;white-space:pre-wrap;overflow-wrap:anywhere}\n.media .dl{font-size:13px;color:var(--accent);margin-top:2px;display:inline-block}\n\n/* ---------- the thinking indicator ---------- */\n.think{max-width:min(92%,640px);color:var(--muted);font-size:15px}\n.think-head{display:flex;align-items:center;gap:9px;background:none;border:0;padding:4px 0;color:inherit;text-align:left}\n.think-head .chev{width:14px;height:14px;opacity:0;transition:transform .2s,opacity .2s}\n.think.done .think-head .chev{opacity:.7}\n.think.done.open .chev{transform:rotate(90deg)}\n.spark{width:19px;height:19px;color:var(--accent);flex:none}\n.think.active .spark{animation:spin 3.2s linear infinite,pulse 1.3s ease-in-out infinite}\n.think-title{font-weight:550}\n.think.active .think-title{\n  background:linear-gradient(90deg,var(--muted) 0%,var(--muted) 35%,var(--fg) 50%,var(--muted) 65%,var(--muted) 100%);\n  background-size:250% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;\n  animation:shimmer 2.1s linear infinite}\n.think-time{font-variant-numeric:tabular-nums;font-size:13px;opacity:.8}\n.think-steps{list-style:none;margin:2px 0 4px 9px;padding:2px 0 2px 18px;border-left:2px solid var(--line)}\n.think-steps li{padding:1px 0;font-size:14px;animation:rise .35s ease both}\n.think.done .think-steps{display:none}\n.think.done.open .think-steps{display:block}\n.think-note{margin:4px 0 0 28px;color:var(--fg);font-size:15px}\n@keyframes spin{to{transform:rotate(360deg)}}\n@keyframes pulse{50%{scale:.78;opacity:.75}}\n@keyframes shimmer{from{background-position:125% 0}to{background-position:-125% 0}}\n@keyframes rise{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}\n@media (prefers-reduced-motion: reduce){\n  .think.active .spark,.think.active .think-title,.think-steps li{animation:none}\n  .think.active .think-title{color:var(--fg);background:none}\n}\n\n/* ---------- empty state + composer ---------- */\n#empty{margin:8vh auto 0;max-width:520px;text-align:center;color:var(--muted)}\n#empty h2{color:var(--fg);font-size:22px;margin:0 0 6px}\n#empty .ex{display:flex;flex-direction:column;gap:8px;margin-top:18px}\n#empty .ex button{border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:10px 14px;text-align:left;color:var(--fg)}\n#empty .ex button:hover{border-color:var(--accent)}\n.hints{display:flex;gap:8px;overflow-x:auto;padding:6px 16px 0;scrollbar-width:none}\n.hints::-webkit-scrollbar{display:none}\n.hints .chip{flex:none;font-size:13px;padding:4px 11px}\n#composer{display:flex;align-items:flex-end;gap:10px;padding:10px 16px;padding-bottom:max(12px,env(safe-area-inset-bottom))}\n#text{flex:1;resize:none;max-height:180px;min-height:46px;padding:11px 14px;border:1px solid var(--line);border-radius:14px;background:var(--panel);outline:none;line-height:1.4}\n#text:focus{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent)}\n#send{width:46px;height:46px;border-radius:14px;border:0;background:var(--accent);color:var(--accent-fg);display:grid;place-items:center;flex:none}\n#send:disabled{opacity:.45;cursor:default}\n#send svg{width:20px;height:20px}\n#toast{position:fixed;left:50%;bottom:92px;transform:translateX(-50%);background:var(--fg);color:var(--bg);padding:8px 16px;border-radius:999px;font-size:14px;opacity:0;pointer-events:none;transition:opacity .2s;max-width:90vw}\n#toast.show{opacity:.95}\n.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}\n</style>\n</head>\n<body>\n\n<svg width=\"0\" height=\"0\" style=\"position:absolute\" aria-hidden=\"true\">\n  <symbol id=\"i-spark\" viewBox=\"0 0 24 24\"><path d=\"M12 2.5v6M12 15.5v6M2.5 12h6M15.5 12h6M5.3 5.3l4.2 4.2M14.5 14.5l4.2 4.2M18.7 5.3l-4.2 4.2M9.5 14.5l-4.2 4.2\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.4\" stroke-linecap=\"round\"/></symbol>\n  <symbol id=\"i-chev\" viewBox=\"0 0 24 24\"><path d=\"M9 5l7 7-7 7\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.4\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></symbol>\n  <symbol id=\"i-send\" viewBox=\"0 0 24 24\"><path d=\"M12 19V5M5 12l7-7 7 7\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.4\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></symbol>\n</svg>\n\n<main id=\"login\" hidden>\n  <form class=\"card\" id=\"loginForm\" autocomplete=\"on\">\n    <h1 class=\"brand\"><svg><use href=\"#i-spark\"/></svg>FreeVideoAgent</h1>\n    <p class=\"sub\">Apne bot se web par baat karo. Login karo.</p>\n    <label for=\"uid\">ID</label>\n    <input class=\"field\" id=\"uid\" name=\"username\" autocomplete=\"username\" autocapitalize=\"none\" spellcheck=\"false\" required>\n    <label for=\"pw\">Password</label>\n    <div class=\"pw\">\n      <input class=\"field\" id=\"pw\" name=\"password\" type=\"password\" autocomplete=\"current-password\" required>\n      <button type=\"button\" id=\"showpw\" aria-label=\"Show password\">Show</button>\n    </div>\n    <button class=\"primary\" id=\"loginBtn\" type=\"submit\">Login</button>\n    <p class=\"err\" id=\"loginErr\" role=\"alert\"></p>\n  </form>\n</main>\n\n<div id=\"app\" hidden>\n  <header>\n    <h1 class=\"brand\"><svg><use href=\"#i-spark\"/></svg>FreeVideoAgent</h1>\n    <span class=\"who\" id=\"who\"></span>\n    <button class=\"ghost\" id=\"clearBtn\" type=\"button\">Clear</button>\n    <button class=\"ghost\" id=\"logoutBtn\" type=\"button\">Logout</button>\n  </header>\n  <div id=\"log\" role=\"log\" aria-live=\"polite\" aria-label=\"Chat\">\n    <div id=\"empty\">\n      <h2>Kya banana hai aaj?</h2>\n      <div>Video, image, research ya koi bhi sawal. Telegram jaisa hi sab kuch yahan chalta hai.</div>\n      <div class=\"ex\" id=\"examples\"></div>\n    </div>\n  </div>\n  <div class=\"hints\" id=\"hints\"></div>\n  <form id=\"composer\">\n    <label class=\"sr\" for=\"text\">Message</label>\n    <textarea id=\"text\" rows=\"1\" placeholder=\"Kuch bhi likho... (jaise: 30 second ka calm English reel banao)\"></textarea>\n    <button id=\"send\" type=\"submit\" aria-label=\"Send\" disabled><svg><use href=\"#i-send\"/></svg></button>\n  </form>\n</div>\n<div id=\"toast\" role=\"status\"></div>\n\n<script>\n\"use strict\";\nconst $ = (id) => document.getElementById(id);\nconst state = { last: 0, user: null, bubbles: new Map(), thinks: new Map(), placeholder: null,\n                timer: null, activity: Date.now(), polling: false };\n\n// ---------------------------------------------------------------- tiny helpers\nfunction esc(s){ return String(s).replace(/[&<>\"']/g, (c) => ({\"&\":\"&amp;\",\"<\":\"&lt;\",\">\":\"&gt;\",'\"':\"&quot;\",\"'\":\"&#39;\"}[c])); }\nfunction inline(p){\n  let s = esc(p);\n  s = s.replace(/`([^`\\n]+)`/g, \"<code>$1</code>\");\n  s = s.replace(/\\*\\*([^*\\n]+)\\*\\*/g, \"<strong>$1</strong>\");\n  s = s.replace(/(https?:\\/\\/[^\\s<]+[^\\s<.,;:!?)\\]'\"])/g, '<a href=\"$1\" target=\"_blank\" rel=\"noopener noreferrer\">$1</a>');\n  return s;\n}\nfunction fmt(text){\n  return String(text).split(\"```\").map((p, i) =>\n    i % 2 ? `<pre><code>${esc(p.replace(/^[\\w+-]*\\n/, \"\").replace(/\\n$/, \"\"))}</code></pre>` : inline(p)).join(\"\");\n}\nfunction dur(sec){\n  sec = Math.max(0, Math.round(sec));\n  const m = Math.floor(sec / 60), s = sec % 60;\n  return m ? `${m}m ${String(s).padStart(2, \"0\")}s` : `${s}s`;\n}\nfunction safeUrl(u){ return typeof u === \"string\" && /^\\/api\\/file\\/f_[0-9a-f]+$/.test(u) ? u : \"\"; }\nfunction toast(text){\n  const t = $(\"toast\"); t.textContent = text; t.classList.add(\"show\");\n  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove(\"show\"), 2600);\n}\nasync function api(path, opts = {}){\n  const res = await fetch(path, { credentials: \"same-origin\", cache: \"no-store\",\n    ...opts, headers: { \"X-Requested-With\": \"fva\", ...(opts.body ? { \"Content-Type\": \"application/json\" } : {}) } });\n  if (res.status === 401 && path !== \"/api/login\") { showLogin(); throw new Error(\"login required\"); }\n  const data = await res.json().catch(() => ({}));\n  if (!res.ok) throw Object.assign(new Error(data.error || res.statusText), { status: res.status });\n  return data;\n}\n\n// ---------------------------------------------------------------- DOM building\nconst log = $(\"log\");\nfunction nearBottom(){ return log.scrollHeight - log.scrollTop - log.clientHeight < 140; }\nfunction place(node){\n  const stick = nearBottom();\n  $(\"empty\").hidden = true;\n  if (state.placeholder && state.placeholder.el.parentNode === log) log.insertBefore(node, state.placeholder.el);\n  else log.appendChild(node);\n  if (stick) log.scrollTop = log.scrollHeight;\n}\nfunction row(cls){ const r = document.createElement(\"div\"); r.className = \"row \" + cls; return r; }\n\nfunction addUser(text){\n  const r = row(\"me\"), b = document.createElement(\"div\");\n  b.className = \"bubble\"; b.innerHTML = fmt(text); r.appendChild(b); place(r);\n}\nfunction fillBot(b, text, buttons){\n  b.innerHTML = fmt(text);\n  if (buttons && buttons.length){\n    const box = document.createElement(\"div\"); box.className = \"btns\";\n    for (const line of buttons) for (const bt of line){\n      const c = document.createElement(\"button\"); c.type = \"button\"; c.className = \"chip\"; c.textContent = bt.t;\n      c.onclick = () => { box.querySelectorAll(\"button\").forEach((x) => x.disabled = true); callback(bt.d, b.dataset.mid); };\n      box.appendChild(c);\n    }\n    b.appendChild(box);\n  }\n}\nfunction addBot(mid, text, buttons){\n  const r = row(\"bot\"), b = document.createElement(\"div\");\n  b.className = \"bubble\"; b.dataset.mid = mid; fillBot(b, text, buttons);\n  r.appendChild(b); place(r); state.bubbles.set(String(mid), b);\n}\nfunction addMedia(kind, d){\n  const url = safeUrl(d.url), r = row(\"bot\"), box = document.createElement(\"div\");\n  box.className = \"media\";\n  if (!url){ box.innerHTML = `<div class=\"bubble\">${fmt(d.caption || \"File not available\")}</div>`; r.appendChild(box); return place(r); }\n  const el = document.createElement(kind === \"video\" ? \"video\" : \"img\");\n  el.src = url;\n  if (kind === \"video\"){ el.controls = true; el.playsInline = true; el.preload = \"metadata\"; }\n  else { el.alt = d.caption || \"image\"; el.loading = \"lazy\"; el.onload = () => { if (nearBottom()) log.scrollTop = log.scrollHeight; }; }\n  if (d.w && d.h) el.style.aspectRatio = d.w + \" / \" + d.h;\n  box.appendChild(el);\n  if (d.caption){ const c = document.createElement(\"div\"); c.className = \"cap\"; c.textContent = d.caption; box.appendChild(c); }\n  const a = document.createElement(\"a\"); a.className = \"dl\"; a.href = url; a.download = kind === \"video\" ? \"video.mp4\" : \"image.jpg\"; a.textContent = \"Download\";\n  box.appendChild(a); r.appendChild(box); place(r);\n}\n\n// ---------------------------------------------------------------- thinking indicator\nfunction makeThinking(title, t0, replay){\n  const el = document.createElement(\"div\");\n  el.className = \"think active\";\n  el.innerHTML = '<button class=\"think-head\" type=\"button\" aria-expanded=\"false\">' +\n    '<svg class=\"spark\"><use href=\"#i-spark\"/></svg><span class=\"think-title\"></span><span class=\"think-time\"></span>' +\n    '<svg class=\"chev\"><use href=\"#i-chev\"/></svg></button><ol class=\"think-steps\"></ol><div class=\"think-note\" hidden></div>';\n  const th = { el, title, t0, done: false, steps: el.querySelector(\".think-steps\"),\n               titleEl: el.querySelector(\".think-title\"), timeEl: el.querySelector(\".think-time\"),\n               note: el.querySelector(\".think-note\"), lastT: t0 };\n  th.titleEl.textContent = (title || \"Thinking\") + \"…\";\n  el.querySelector(\".think-head\").onclick = () => {\n    if (!th.done) return;\n    const open = el.classList.toggle(\"open\");\n    el.querySelector(\".think-head\").setAttribute(\"aria-expanded\", open);\n  };\n  const r = row(\"bot\"); r.appendChild(el); place(r);\n  tick(th);\n  return th;\n}\nfunction tick(th){\n  if (th.done) return;\n  th.timeEl.textContent = dur(Date.now() / 1000 - th.t0);\n}\nfunction finishThinking(th, final, t){\n  if (th.done) return;\n  th.done = true;\n  const took = Math.max(0, (t || Date.now() / 1000) - th.t0);\n  th.el.classList.remove(\"active\"); th.el.classList.add(\"done\");\n  th.titleEl.textContent = th.title === \"Thinking\" ? \"Thought for \" + dur(took) : (th.title || \"Done\") + \" · \" + dur(took);\n  th.timeEl.textContent = \"\";\n  if (final){ th.note.hidden = false; th.note.textContent = final; }\n  if (!th.steps.children.length) th.el.querySelector(\".chev\").style.display = \"none\";\n}\nfunction thinkFor(tid, ev, replay){\n  let th = state.thinks.get(tid);\n  if (!th){ th = makeThinking(ev.data.title || \"Thinking\", ev.t, replay); state.thinks.set(tid, th); }\n  th.lastT = ev.t;\n  return th;\n}\nsetInterval(() => { for (const th of state.thinks.values()) tick(th); }, 1000);\n\nfunction showPlaceholder(){\n  clearPlaceholder();\n  const th = makeThinking(\"Thinking\", Date.now() / 1000, false);\n  const stale = setTimeout(() => {\n    if (state.placeholder && state.placeholder.th === th){\n      th.titleEl.textContent = \"Waiting for the bot… (it may be restarting, your message is queued)\";\n    }\n  }, 45000);\n  state.placeholder = { th, el: th.el.parentNode, stale };\n}\nfunction clearPlaceholder(){\n  const p = state.placeholder; if (!p) return;\n  clearTimeout(p.stale); p.el.remove(); state.placeholder = null;\n}\n\n// ---------------------------------------------------------------- applying server events\nfunction apply(ev, replay){\n  const d = ev.data || {};\n  state.activity = Date.now();\n  if (ev.type !== \"user\") clearPlaceholder();\n  switch (ev.type){\n    case \"user\": addUser(d.text || \"\"); break;\n    case \"msg\": addBot(d.mid, d.text || \"\", d.buttons); break;\n    case \"edit\": {\n      const b = state.bubbles.get(String(d.mid));\n      if (b) fillBot(b, d.text || \"\", d.buttons); else addBot(d.mid, d.text || \"\", d.buttons);\n      break; }\n    case \"del\": { const b = state.bubbles.get(String(d.mid)); if (b){ b.closest(\".row\").remove(); state.bubbles.delete(String(d.mid)); } break; }\n    case \"toast\": if (!replay) toast(d.text || \"\"); break;\n    case \"image\": addMedia(\"image\", d); break;\n    case \"video\": addMedia(\"video\", d); break;\n    case \"think_start\": thinkFor(d.tid, ev, replay); break;\n    case \"think_step\": {\n      const th = thinkFor(d.tid, ev, replay);\n      const li = document.createElement(\"li\"); li.textContent = d.text || \"\"; th.steps.appendChild(li);\n      if (nearBottom()) log.scrollTop = log.scrollHeight;\n      break; }\n    case \"think_stop\": { const th = thinkFor(d.tid, ev, replay); finishThinking(th, d.final, ev.t); break; }\n  }\n}\nfunction settleStale(){            // a thinking that never got a stop (bot crashed) must not spin forever\n  const cutoff = Date.now() / 1000 - 20 * 60;\n  for (const th of state.thinks.values()) if (!th.done && th.lastT < cutoff) finishThinking(th, \"Stopped (no update for 20 minutes)\", th.lastT);\n}\nfunction busy(){ return !!state.placeholder || [...state.thinks.values()].some((t) => !t.done); }\n\n// ---------------------------------------------------------------- polling\nasync function poll(){\n  clearTimeout(state.timer);\n  let failed = false;\n  try {\n    const r = await api(\"/api/events?after=\" + state.last);\n    for (const ev of r.events) apply(ev, false);\n    state.last = r.last; settleStale();\n  } catch (e) { failed = true; }\n  const idle = (Date.now() - state.activity) / 1000;\n  let wait = busy() ? 900 : idle > 120 ? 10000 : 3500;\n  if (document.hidden) wait = Math.max(wait, 15000);\n  if (failed) wait = 6000;\n  if (state.user) state.timer = setTimeout(poll, wait);\n}\ndocument.addEventListener(\"visibilitychange\", () => { if (!document.hidden && state.user) poll(); });\n\n// ---------------------------------------------------------------- sending\nasync function send(text){\n  text = text.trim(); if (!text) return;\n  showPlaceholder();\n  try { await api(\"/api/send\", { method: \"POST\", body: JSON.stringify({ text }) }); }\n  catch (e) { clearPlaceholder(); toast(e.status === 429 ? \"Thoda dheere... bahut zyada messages\" : \"Send nahi hua: \" + e.message); return; }\n  poll();\n}\nasync function callback(data, mid){\n  try { await api(\"/api/send\", { method: \"POST\", body: JSON.stringify({ callback: { data, mid: Number(mid) || 0 } }) }); }\n  catch (e) { toast(\"Failed: \" + e.message); return; }\n  poll();\n}\n\n// ---------------------------------------------------------------- views\nfunction showLogin(){\n  state.user = null; clearTimeout(state.timer);\n  $(\"app\").hidden = true; $(\"login\").hidden = false; $(\"uid\").focus();\n}\nasync function showApp(user){\n  state.user = user; state.last = 0; state.bubbles.clear(); state.thinks.clear(); state.placeholder = null;\n  [...log.children].forEach((c) => { if (c.id !== \"empty\") c.remove(); });\n  $(\"empty\").hidden = false;\n  $(\"who\").textContent = user;\n  $(\"login\").hidden = true; $(\"app\").hidden = false;\n  try {\n    const r = await api(\"/api/history\");\n    for (const ev of r.events) apply(ev, true);\n    state.last = r.last; settleStale();\n  } catch (e) { return; }\n  log.scrollTop = log.scrollHeight;\n  poll(); $(\"text\").focus();\n}\n\n$(\"loginForm\").addEventListener(\"submit\", async (e) => {\n  e.preventDefault();\n  const btn = $(\"loginBtn\"), err = $(\"loginErr\");\n  btn.disabled = true; err.textContent = \"\"; btn.textContent = \"Checking…\";\n  try {\n    const r = await api(\"/api/login\", { method: \"POST\", body: JSON.stringify({ id: $(\"uid\").value, password: $(\"pw\").value }) });\n    $(\"pw\").value = \"\"; await showApp(r.user);\n  } catch (ex) { err.textContent = ex.message; }\n  finally { btn.disabled = false; btn.textContent = \"Login\"; }\n});\n$(\"showpw\").onclick = () => { const p = $(\"pw\"), show = p.type === \"password\"; p.type = show ? \"text\" : \"password\"; $(\"showpw\").textContent = show ? \"Hide\" : \"Show\"; };\n$(\"logoutBtn\").onclick = async () => { try { await api(\"/api/logout\", { method: \"POST\", body: \"{}\" }); } catch {} showLogin(); };\n$(\"clearBtn\").onclick = async () => {\n  if (!confirm(\"Is chat ki history saaf kar dun?\")) return;\n  try { await api(\"/api/clear\", { method: \"POST\", body: \"{}\" }); await showApp(state.user); } catch (e) { toast(e.message); }\n};\n\nconst ta = $(\"text\");\nfunction grow(){ ta.style.height = \"auto\"; ta.style.height = Math.min(ta.scrollHeight, 180) + \"px\"; $(\"send\").disabled = !ta.value.trim(); }\nta.addEventListener(\"input\", grow);\nta.addEventListener(\"keydown\", (e) => {\n  if (e.key === \"Enter\" && !e.shiftKey && !e.isComposing){ e.preventDefault(); $(\"composer\").requestSubmit(); }\n});\n$(\"composer\").addEventListener(\"submit\", (e) => {\n  e.preventDefault(); const t = ta.value; if (!t.trim()) return;\n  ta.value = \"\"; grow(); send(t);\n});\n\nfor (const c of [\"/video \", \"/image \", \"/ask \", \"/research \", \"/trends\", \"/status\", \"/settings\", \"/skills\", \"/notes\", \"/help\"]){\n  const b = document.createElement(\"button\"); b.type = \"button\"; b.className = \"chip\"; b.textContent = c.trim();\n  b.onclick = () => { if (c.endsWith(\" \")){ ta.value = c; grow(); ta.focus(); } else send(c); };\n  $(\"hints\").appendChild(b);\n}\nfor (const ex of [\"30 second ka Hindi motivational reel banao, calm music\", \"20 second calm English reel\", \"/trends\", \"/research AI video tools\"]){\n  const b = document.createElement(\"button\"); b.type = \"button\"; b.textContent = ex; b.onclick = () => send(ex);\n  $(\"examples\").appendChild(b);\n}\n\n(async () => {\n  try { const me = await api(\"/api/me\"); await showApp(me.user); } catch { showLogin(); }\n})();\n</script>\n</body>\n</html>\n";

const enc = new TextEncoder();
const SESSION_SECONDS = 7 * 24 * 3600;
const FILE_TTL = 7 * 24 * 3600;
const MAX_FILE = 24 * 1024 * 1024;           // KV value limit is 25 MiB
const RANGE_CAP = 2 * 1024 * 1024;           // answer Range requests in <= 2 MiB pieces (keeps CPU tiny)
const EVENT_TYPES = new Set(["user", "msg", "edit", "del", "toast", "image", "video",
                             "think_start", "think_step", "think_stop"]);

const SCHEMA = [
  "CREATE TABLE IF NOT EXISTS web_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, uname TEXT NOT NULL, uid INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', created_at INTEGER NOT NULL)",
  "CREATE INDEX IF NOT EXISTS idx_web_inbox_status ON web_inbox(status, id)",
  "CREATE TABLE IF NOT EXISTS web_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, uname TEXT NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL, created_at INTEGER NOT NULL)",
  "CREATE INDEX IF NOT EXISTS idx_web_events_user ON web_events(uname, seq)",
  "CREATE TABLE IF NOT EXISTS web_login_fail (ip TEXT NOT NULL, uname TEXT, ts INTEGER NOT NULL)",
  "CREATE INDEX IF NOT EXISTS idx_web_login_fail ON web_login_fail(ts)",
];
let schemaReady = false;

// ------------------------------------------------------------------ helpers
const now = () => Math.floor(Date.now() / 1000);

function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extra },
  });
}

function b64u(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function unb64u(str) {
  const s = str.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((str.length + 3) % 4);
  const bin = atob(s);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

async function hmac(secret, message) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(message)));
}

function sameBytes(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
  return d === 0;
}

// Compares two secrets without leaking where they differ (both are hashed to 32 bytes first).
async function secretsEqual(given, expected) {
  const k = "compare";
  return sameBytes(await hmac(k + (expected || ""), given || ""), await hmac(k + (expected || ""), expected || ""))
    && !!expected;
}

function users(env) {
  const out = {};
  if (env.WEB_USER && env.WEB_PASSWORD) {
    out[String(env.WEB_USER).trim().toLowerCase()] = { password: String(env.WEB_PASSWORD), uid: Number(env.WEB_UID || 0) };
  }
  if (env.WEB_USERS) {
    try {
      for (const [name, v] of Object.entries(JSON.parse(env.WEB_USERS))) {
        out[name.trim().toLowerCase()] = { password: String(v.password || ""), uid: Number(v.uid || 0) };
      }
    } catch { /* ignore a broken WEB_USERS, the single user (if any) still works */ }
  }
  return out;
}

function clientIp(request) {
  return request.headers.get("CF-Connecting-IP") || request.headers.get("X-Forwarded-For") || "local";
}

async function ensureSchema(env) {
  if (schemaReady) return;
  await env.DB.batch(SCHEMA.map((s) => env.DB.prepare(s)));
  schemaReady = true;
}

// ------------------------------------------------------------------ sessions
async function makeSession(env, user) {
  const payload = b64u(enc.encode(JSON.stringify({ u: user, exp: now() + SESSION_SECONDS })));
  return payload + "." + b64u(await hmac(env.SESSION_SECRET, payload));
}

async function readSession(request, env) {
  const m = /(?:^|;\s*)fva=([^;]+)/.exec(request.headers.get("Cookie") || "");
  if (!m || !env.SESSION_SECRET) return null;
  const [payload, sig] = m[1].split(".");
  if (!payload || !sig) return null;
  let given;
  try { given = unb64u(sig); } catch { return null; }
  if (!sameBytes(given, await hmac(env.SESSION_SECRET, payload))) return null;
  try {
    const data = JSON.parse(new TextDecoder().decode(unb64u(payload)));
    if (!data.u || data.exp < now() || !users(env)[data.u]) return null;
    return data.u;
  } catch { return null; }
}

function cookieHeader(env, value, maxAge) {
  const secure = env.DEV === "1" ? "" : " Secure;";
  return `fva=${value}; Path=/; HttpOnly;${secure} SameSite=Strict; Max-Age=${maxAge}`;
}

// ------------------------------------------------------------------ routing
export default {
  async fetch(request, env) {
    try {
      return await route(request, env);
    } catch (e) {
      console.error("worker error", e && e.stack || e);
      return json({ error: "server error" }, 500);
    }
  },
};

async function route(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  if (request.method === "GET" && (path === "/" || path === "/index.html")) {
    return new Response(INDEX_HTML, { headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
      "X-Frame-Options": "DENY",
      "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; " +
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; " +
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    } });
  }
  if (path === "/healthz") return new Response("ok");
  if (!env.DB) return json({ error: "D1 binding 'DB' is missing (see web/README)" }, 500);
  await ensureSchema(env);
  if (path.startsWith("/bridge/")) return bridge(request, env, url);
  if (path.startsWith("/api/")) return api(request, env, url);
  return new Response("Not found", { status: 404 });
}

// ------------------------------------------------------------------ browser API
async function api(request, env, url) {
  const path = url.pathname;
  const method = request.method;

  if (method === "POST") {                           // CSRF: a custom header cannot be sent cross-site
    if (request.headers.get("X-Requested-With") !== "fva") return json({ error: "bad request" }, 400);
    const origin = request.headers.get("Origin");
    if (origin && new URL(origin).host !== url.host) return json({ error: "bad origin" }, 403);
  }

  if (path === "/api/login" && method === "POST") return login(request, env);
  if (path === "/api/logout" && method === "POST") {
    return json({ ok: true }, 200, { "Set-Cookie": cookieHeader(env, "", 0) });
  }

  const user = await readSession(request, env);
  if (!user) return json({ error: "login required" }, 401);
  const me = users(env)[user];

  if (path === "/api/me" && method === "GET") return json({ user });

  if (path === "/api/send" && method === "POST") {
    let body;
    try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
    const t = now();
    const recent = await env.DB.prepare("SELECT COUNT(*) AS n FROM web_inbox WHERE uname=? AND created_at>?").bind(user, t - 60).first();
    const waiting = await env.DB.prepare("SELECT COUNT(*) AS n FROM web_inbox WHERE uname=? AND status='new'").bind(user).first();
    if ((recent?.n || 0) >= 30 || (waiting?.n || 0) >= 20) return json({ error: "slow down" }, 429);
    if (body && body.callback && typeof body.callback.data === "string") {
      const cb = { data: body.callback.data.slice(0, 200), mid: Number(body.callback.mid) || 0 };
      await env.DB.prepare("INSERT INTO web_inbox (uname, uid, kind, payload, created_at) VALUES (?,?,?,?,?)")
        .bind(user, me.uid, "callback", JSON.stringify(cb), t).run();
      return json({ ok: true });
    }
    const text = String((body && body.text) || "").trim().slice(0, 4000);
    if (!text) return json({ error: "empty message" }, 400);
    await env.DB.batch([
      env.DB.prepare("INSERT INTO web_inbox (uname, uid, kind, payload, created_at) VALUES (?,?,?,?,?)")
        .bind(user, me.uid, "text", JSON.stringify({ text }), t),
      env.DB.prepare("INSERT INTO web_events (uname, type, data, created_at) VALUES (?,?,?,?)")
        .bind(user, "user", JSON.stringify({ text }), t),
    ]);
    return json({ ok: true });
  }

  if (path === "/api/events" && method === "GET") {
    const after = parseInt(url.searchParams.get("after") || "0", 10) || 0;
    const limit = Math.min(parseInt(url.searchParams.get("limit") || "200", 10) || 200, 300);
    const rs = await env.DB.prepare("SELECT seq, type, data, created_at FROM web_events WHERE uname=? AND seq>? ORDER BY seq LIMIT ?")
      .bind(user, after, limit).all();
    return json(eventsPayload(rs.results || [], after));
  }

  if (path === "/api/history" && method === "GET") {
    const rs = await env.DB.prepare("SELECT seq, type, data, created_at FROM web_events WHERE uname=? ORDER BY seq DESC LIMIT 150")
      .bind(user).all();
    return json(eventsPayload((rs.results || []).reverse(), 0));
  }

  if (path === "/api/clear" && method === "POST") {
    await env.DB.prepare("DELETE FROM web_events WHERE uname=?").bind(user).run();
    return json({ ok: true });
  }

  if (path.startsWith("/api/file/") && method === "GET") return serveFile(request, env, user, path.slice(10));
  return json({ error: "not found" }, 404);
}

function eventsPayload(rows, after) {
  const events = rows.map((r) => {
    let data = {};
    try { data = JSON.parse(r.data); } catch { /* keep {} */ }
    return { seq: r.seq, type: r.type, data, t: r.created_at };
  });
  return { events, last: events.length ? events[events.length - 1].seq : after };
}

async function login(request, env) {
  if (!env.SESSION_SECRET) return json({ error: "SESSION_SECRET is not set on the Worker" }, 500);
  const all = users(env);
  if (!Object.keys(all).length) return json({ error: "No web user configured (set WEB_USER, WEB_PASSWORD, WEB_UID)" }, 500);
  let body;
  try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
  const name = String(body.id || "").trim().toLowerCase().slice(0, 64);
  const pass = String(body.password || "").slice(0, 256);
  const ip = clientIp(request);
  const t = now();

  const byIp = await env.DB.prepare("SELECT COUNT(*) AS n FROM web_login_fail WHERE ip=? AND ts>?").bind(ip, t - 600).first();
  const byName = await env.DB.prepare("SELECT COUNT(*) AS n FROM web_login_fail WHERE uname=? AND ts>?").bind(name, t - 600).first();
  if ((byIp?.n || 0) >= 8 || (byName?.n || 0) >= 20) {
    return json({ error: "Too many wrong attempts. Try again in 10 minutes." }, 429);
  }

  const account = all[name];
  const ok = await secretsEqual(pass, account ? account.password : "\u0000no-such-user\u0000") && !!account;
  if (!ok) {
    await env.DB.prepare("INSERT INTO web_login_fail (ip, uname, ts) VALUES (?,?,?)").bind(ip, name, t).run();
    return json({ error: "Wrong ID or password" }, 401);
  }
  return json({ ok: true, user: name }, 200, { "Set-Cookie": cookieHeader(env, await makeSession(env, name), SESSION_SECONDS) });
}

async function serveFile(request, env, user, id) {
  if (!env.FILES) return json({ error: "KV binding 'FILES' is missing" }, 500);
  if (!/^f_[0-9a-f]{16,32}$/.test(id)) return json({ error: "not found" }, 404);
  const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.get("Range") || "");
  const base = { "Content-Disposition": "inline", "Cache-Control": "private, max-age=3600",
                 "X-Content-Type-Options": "nosniff", "Accept-Ranges": "bytes" };

  if (!range) {
    const hit = await env.FILES.getWithMetadata(id, { type: "stream" });
    if (!hit.value || !hit.metadata || hit.metadata.u !== user) return json({ error: "not found" }, 404);
    return new Response(hit.value, { headers: { ...base, "Content-Type": hit.metadata.mime || "application/octet-stream",
                                                  "Content-Length": String(hit.metadata.size || "") } });
  }
  const meta = await env.FILES.getWithMetadata(id, { type: "arrayBuffer" });
  if (!meta.value || !meta.metadata || meta.metadata.u !== user) return json({ error: "not found" }, 404);
  const total = meta.value.byteLength;
  let start = range[1] === "" ? Math.max(total - parseInt(range[2] || "0", 10), 0) : parseInt(range[1], 10);
  let end = range[1] === "" || range[2] === "" ? total - 1 : parseInt(range[2], 10);
  end = Math.min(end, total - 1, start + RANGE_CAP - 1);
  if (!(start >= 0 && start <= end)) {
    return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${total}` } });
  }
  return new Response(meta.value.slice(start, end + 1), { status: 206, headers: {
    ...base, "Content-Type": meta.metadata.mime || "application/octet-stream",
    "Content-Range": `bytes ${start}-${end}/${total}`, "Content-Length": String(end - start + 1) } });
}

// ------------------------------------------------------------------ bot bridge
async function bridge(request, env, url) {
  const auth = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  if (!env.BRIDGE_SECRET || !(await secretsEqual(auth, env.BRIDGE_SECRET))) return json({ error: "forbidden" }, 403);
  const path = url.pathname;
  const all = users(env);

  if (path === "/bridge/next" && request.method === "GET") {
    const wait = Math.min(Math.max(parseInt(url.searchParams.get("wait") || "0", 10) || 0, 0), 20);
    const deadline = Date.now() + wait * 1000;
    for (;;) {
      const rs = await env.DB.prepare(
        "UPDATE web_inbox SET status='taken' WHERE id = (SELECT id FROM web_inbox WHERE status='new' ORDER BY id LIMIT 1) " +
        "RETURNING id, uname, uid, kind, payload").all();
      const row = (rs.results || [])[0];
      if (row) {
        let payload = {};
        try { payload = JSON.parse(row.payload); } catch { /* keep {} */ }
        return json({ id: row.id, user: row.uname, uid: row.uid, kind: row.kind, ...payload });
      }
      if (Date.now() + 1300 > deadline) return new Response(null, { status: 204 });
      await new Promise((r) => setTimeout(r, 1200));
    }
  }

  if (path === "/bridge/event" && request.method === "POST") {
    let body;
    try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
    const user = String(body.user || "").toLowerCase();
    if (!all[user]) return json({ error: "unknown user" }, 400);
    if (!EVENT_TYPES.has(body.type)) return json({ error: "unknown event type" }, 400);
    const data = JSON.stringify(body.data && typeof body.data === "object" ? body.data : {});
    if (data.length > 30000) return json({ error: "event too large" }, 413);
    const r = await env.DB.prepare("INSERT INTO web_events (uname, type, data, created_at) VALUES (?,?,?,?)")
      .bind(user, body.type, data, now()).run();
    if (Math.random() < 0.02) await housekeeping(env);
    return json({ ok: true, seq: r.meta ? r.meta.last_row_id : 0 });
  }

  if (path === "/bridge/file" && request.method === "PUT") {
    if (!env.FILES) return json({ error: "KV binding 'FILES' is missing" }, 500);
    const user = String(url.searchParams.get("user") || "").toLowerCase();
    if (!all[user]) return json({ error: "unknown user" }, 400);
    const bytes = await request.arrayBuffer();
    if (!bytes.byteLength || bytes.byteLength > MAX_FILE) return json({ error: "file empty or over 24 MB" }, 413);
    const mime = String(url.searchParams.get("mime") || "application/octet-stream").slice(0, 100);
    if (!/^(image|video)\/[a-z0-9.+-]+$/i.test(mime)) return json({ error: "only image/video files" }, 400);
    const id = "f_" + [...crypto.getRandomValues(new Uint8Array(12))].map((b) => b.toString(16).padStart(2, "0")).join("");
    await env.FILES.put(id, bytes, { expirationTtl: FILE_TTL, metadata: { u: user, mime, size: bytes.byteLength } });
    return json({ ok: true, url: "/api/file/" + id });
  }
  return json({ error: "not found" }, 404);
}

async function housekeeping(env) {
  const t = now();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM web_events WHERE created_at < ?").bind(t - 14 * 86400),
    env.DB.prepare("DELETE FROM web_inbox WHERE created_at < ?").bind(t - 2 * 86400),
    env.DB.prepare("DELETE FROM web_login_fail WHERE ts < ?").bind(t - 86400),
  ]);
}
