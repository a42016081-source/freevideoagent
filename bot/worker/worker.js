// Cloudflare Worker: instant Telegram -> GitHub relay (free tier).
//
// Telegram webhook  ->  this Worker  ->  GitHub repository_dispatch  ->  bot_update.yml runs your bot code.
// Only updates from ALLOWED_USER_IDS are forwarded, so strangers cannot burn your Actions minutes.
//
// Secrets / variables to set on the Worker (wrangler secret put NAME):
//   GITHUB_TOKEN     fine-grained PAT, only this repo, permission "Contents: Read and write"
//   GITHUB_REPO      owner/repo
//   WEBHOOK_SECRET   any long random string (same value you pass to Telegram setWebhook)
//   ALLOWED_USER_IDS comma-separated Telegram user IDs

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("FreeVideoAgent relay is up");

    if (!env.WEBHOOK_SECRET ||
        request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try { update = await request.json(); } catch { return new Response("bad request", { status: 400 }); }

    const sender = (update.message || update.callback_query || {}).from;
    const allowed = (env.ALLOWED_USER_IDS || "").split(",").map((s) => s.trim()).filter(Boolean);
    if (!sender || !allowed.includes(String(sender.id))) {
      return new Response("ok"); // silently ignore everyone else
    }

    const dispatch = fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "freevideoagent-relay",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ event_type: "telegram_update", client_payload: { update } }),
    });
    ctx.waitUntil(dispatch);          // answer Telegram immediately, finish the GitHub call in the background
    return new Response("ok");
  },
};
