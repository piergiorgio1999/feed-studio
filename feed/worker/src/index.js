import html from "./index.html";
import videos from "./videos.json";
const ok = (req, env) => {
  const u = new URL(req.url), c = /(?:^|; )k=([^;]+)/.exec(req.headers.get("Cookie") || "");
  return (u.searchParams.get("k") || c?.[1]) === env.ACCESS_TOKEN;
};
export default {
  async fetch(req, env) {
    const u = new URL(req.url);
    if (!env.ACCESS_TOKEN || !ok(req, env)) return new Response("Accesso negato", { status: 401 });
    const h = { "Set-Cookie": `k=${env.ACCESS_TOKEN}; Path=/; Max-Age=31536000; Secure; HttpOnly; SameSite=Strict` };
    if (u.pathname === "/") return new Response(html, { headers: { ...h, "Content-Type": "text/html; charset=utf-8" } });
    if (u.pathname === "/api/videos") return Response.json(videos, { headers: h });
    if (u.pathname.startsWith("/api/") && u.pathname !== "/api/videos") {
      const db = env.DB, j = req.method === "POST" ? await req.json().catch(() => ({})) : {};
      if (u.pathname === "/api/social") {
        const likes = (await db.prepare("SELECT video_id FROM likes").all()).results.map(r => r.video_id);
        const cc = (await db.prepare("SELECT video_id, COUNT(*) n FROM comments GROUP BY video_id").all()).results;
        return Response.json({ likes, comments: Object.fromEntries(cc.map(r => [r.video_id, r.n])) });
      }
      if (u.pathname === "/api/like" && req.method === "POST") {
        const id = +j.id, had = await db.prepare("SELECT 1 FROM likes WHERE video_id=?").bind(id).first();
        await db.prepare(had ? "DELETE FROM likes WHERE video_id=?" : "INSERT INTO likes(video_id) VALUES(?)").bind(id).run();
        return Response.json({ liked: !had });
      }
      if (u.pathname === "/api/comments" && req.method === "GET") {
        const q = u.searchParams, w = [], b = [];
        if (q.get("video")) { w.push("video_id=?"); b.push(+q.get("video")); }
        if (q.get("target")) { w.push("target=?"); b.push(q.get("target")); } else if (q.get("flagged")) w.push("flagged=1");
        if (q.get("status")) { w.push("status=?"); b.push(q.get("status")); }
        const r = await db.prepare(`SELECT * FROM comments ${w.length ? "WHERE " + w.join(" AND ") : ""} ORDER BY id`).bind(...b).all();
        return Response.json(r.results);
      }
      if (u.pathname === "/api/comment" && req.method === "POST") {
        const t = String(j.text || "").trim().slice(0, 2000);
        if (!t) return new Response("vuoto", { status: 400 });
        const r = await db.prepare("INSERT INTO comments(video_id,text,flagged,target) VALUES(?,?,?,?)").bind(+j.id, t, ["claude","codex"].includes(j.target) ? 1 : 0, ["claude","codex"].includes(j.target) ? j.target : null).run();
        return Response.json({ id: r.meta.last_row_id });
      }
      if (u.pathname === "/api/comment/update" && req.method === "POST") {   // per Claude: status / reply
        await db.prepare("UPDATE comments SET status=COALESCE(?,status), reply=COALESCE(?,reply) WHERE id=?").bind(j.status ?? null, j.reply ?? null, +j.id).run();
        return Response.json({ ok: true });
      }
      return new Response("Not found", { status: 404 });
    }
    const m = /^\/video\/(\d+)$/.exec(u.pathname);
    if (m) {
      const r = req.headers.get("Range");
      const o = await env.VIDEOS.get(`v/${m[1]}.mp4`, r ? { range: req.headers } : {});
      if (!o) return new Response("Not found", { status: 404 });
      const hd = new Headers({ "Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Cache-Control": "private, max-age=86400" });
      if (o.range && r) {
        const s = o.range.offset ?? 0, e = o.range.end ?? o.size - 1;
        hd.set("Content-Range", `bytes ${s}-${e}/${o.size}`); hd.set("Content-Length", String(e - s + 1));
        return new Response(o.body, { status: 206, headers: hd });
      }
      hd.set("Content-Length", String(o.size));
      return new Response(o.body, { headers: hd });
    }
    return new Response("Not found", { status: 404 });
  },
};
