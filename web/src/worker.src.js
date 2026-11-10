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

const INDEX_HTML = /*__INDEX_HTML__*/ "";

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
