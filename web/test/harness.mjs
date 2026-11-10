// Runs web/worker.js locally with a fake D1 (node:sqlite) and a fake KV, for tests and screenshots.
//   SESSION_SECRET=.. BRIDGE_SECRET=.. WEB_USER=jon WEB_PASSWORD=.. WEB_UID=111 node test/harness.mjs 8787
import http from "node:http";
import { DatabaseSync } from "node:sqlite";
import worker from "../worker.js";

class Stmt {
  constructor(db, sql, args = []) { this.db = db; this.sql = sql; this.args = args; }
  bind(...args) { return new Stmt(this.db, this.sql, args); }
  async first() { return this.db.prepare(this.sql).get(...this.args) ?? null; }
  async all() { return { results: this.db.prepare(this.sql).all(...this.args), success: true, meta: {} }; }
  async run() {
    const r = this.db.prepare(this.sql).run(...this.args);
    return { results: [], success: true, meta: { last_row_id: Number(r.lastInsertRowid), changes: Number(r.changes) } };
  }
}
class FakeD1 {
  constructor() { this.db = new DatabaseSync(":memory:"); }
  prepare(sql) { return new Stmt(this.db, sql); }
  async batch(stmts) { const out = []; for (const s of stmts) out.push(await s.run()); return out; }
}
class FakeKV {
  constructor() { this.m = new Map(); }
  async put(k, v, opts = {}) { this.m.set(k, { v: Buffer.from(v), meta: opts.metadata }); }
  async getWithMetadata(k, { type } = {}) {
    const e = this.m.get(k);
    if (!e) return { value: null, metadata: null };
    if (type === "stream") return { value: new Response(e.v).body, metadata: e.meta };
    return { value: e.v.buffer.slice(e.v.byteOffset, e.v.byteOffset + e.v.byteLength), metadata: e.meta };
  }
}

const env = {
  DB: new FakeD1(), FILES: new FakeKV(), DEV: "1",
  SESSION_SECRET: process.env.SESSION_SECRET || "test-session-secret",
  BRIDGE_SECRET: process.env.BRIDGE_SECRET || "test-bridge-secret",
  WEB_USER: process.env.WEB_USER || "jon", WEB_PASSWORD: process.env.WEB_PASSWORD || "pass123",
  WEB_UID: process.env.WEB_UID || "111",
};

const server = http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const url = `http://${req.headers.host}${req.url}`;
  const init = { method: req.method, headers: req.headers };
  if (body && !["GET", "HEAD"].includes(req.method)) init.body = body;
  const r = await worker.fetch(new Request(url, init), env);
  const headers = {};
  r.headers.forEach((v, k) => { headers[k] = v; });
  const sc = r.headers.getSetCookie ? r.headers.getSetCookie() : [];
  if (sc.length) headers["set-cookie"] = sc;
  res.writeHead(r.status, headers);
  res.end(Buffer.from(await r.arrayBuffer()));
});
const port = Number(process.argv[2] || 8787);
server.listen(port, "127.0.0.1", () => console.log(`listening http://127.0.0.1:${port}`));
