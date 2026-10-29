// Cloudflare Pages Functions middleware: shared-password gate + API proxy.
//
//   /login            GET shows the password form, POST checks DEMO_PASSWORD and sets a signed cookie (7 days)
//   /logout           clears the cookie
//   /api/*            with a valid cookie: proxied to API_ORIGIN (Railway) with the x-umpyr-gateway header
//   everything else   with a valid cookie: served from the static build; otherwise redirected to /login
//
// Environment (Pages project > Settings > Variables and Secrets):
//   DEMO_PASSWORD       the shared password (secret)
//   SESSION_SECRET      random string used to sign the cookie (secret)
//   API_ORIGIN          e.g. https://umpyr-engine-production.up.railway.app  (no trailing slash)
//   API_GATEWAY_SECRET  must equal API_GATEWAY_SECRET on the Railway service (secret)

type Env = {
  DEMO_PASSWORD: string;
  SESSION_SECRET: string;
  API_ORIGIN: string;
  API_GATEWAY_SECRET: string;
};

const COOKIE = "umpyr_demo";
const TTL_SECONDS = 7 * 24 * 3600;
const enc = new TextEncoder();

async function hmac(secret: string, msg: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(msg));
  return btoa(String.fromCharCode(...new Uint8Array(sig))).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function makeCookie(env: Env): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + TTL_SECONDS;
  const sig = await hmac(env.SESSION_SECRET, String(exp));
  return `${COOKIE}=${exp}.${sig}; Path=/; Max-Age=${TTL_SECONDS}; HttpOnly; Secure; SameSite=Lax`;
}

async function hasValidCookie(request: Request, env: Env): Promise<boolean> {
  const raw = request.headers.get("cookie") ?? "";
  const m = raw.match(new RegExp(`(?:^|;\\s*)${COOKIE}=([^;]+)`));
  if (!m) return false;
  const [expStr, sig] = m[1].split(".");
  const exp = Number(expStr);
  if (!exp || !sig || exp < Date.now() / 1000) return false;
  return timingSafeEqual(await hmac(env.SESSION_SECRET, expStr), sig);
}

function loginPage(next: string, error = ""): Response {
  const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/";
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Umpyr demo</title>
<style>
  body{margin:0;min-height:100vh;display:grid;place-items:center;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f8f8;color:#12302e}
  form{background:#fff;border:1px solid #d9e2e1;border-radius:12px;padding:28px 32px;width:min(360px,90vw);box-shadow:0 6px 24px rgba(18,48,46,.06)}
  h1{font-size:18px;margin:0 0 4px}p{margin:0 0 16px;color:#5b6f6d}
  input{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #c5d3d1;border-radius:8px;font-size:15px}
  button{margin-top:12px;width:100%;padding:10px;border:0;border-radius:8px;background:#0f766e;color:#fff;font-size:15px;cursor:pointer}
  .err{color:#b45309;margin:8px 0 0;font-size:14px}
</style></head><body>
<form method="post" action="/login">
  <h1>Umpyr Resolve demo</h1>
  <p>Enter the access password you were given.</p>
  <input type="password" name="password" autocomplete="current-password" autofocus required>
  <input type="hidden" name="next" value="${safeNext.replace(/"/g, "&quot;")}">
  ${error ? `<p class="err">${error}</p>` : ""}
  <button type="submit">Enter</button>
</form></body></html>`;
  return new Response(html, { status: error ? 401 : 200, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}

async function proxyApi(request: Request, env: Env, url: URL): Promise<Response> {
  if (!env.API_ORIGIN) return new Response(JSON.stringify({ error: "API_ORIGIN not configured" }), { status: 500 });
  const target = env.API_ORIGIN.replace(/\/$/, "") + url.pathname.replace(/^\/api/, "") + url.search;
  const headers = new Headers();
  for (const h of ["content-type", "accept", "cache-control", "last-event-id"]) {
    const v = request.headers.get(h);
    if (v) headers.set(h, v);
  }
  headers.set("x-umpyr-gateway", env.API_GATEWAY_SECRET ?? "");
  const init: RequestInit = { method: request.method, headers, redirect: "manual" };
  if (!["GET", "HEAD"].includes(request.method)) init.body = request.body;
  const upstream = await fetch(target, init);
  const out = new Headers(upstream.headers);
  out.delete("access-control-allow-origin");
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const onRequest: PagesFunction<Env>[] = [
  async ({ request, env, next }) => {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/logout") {
      return new Response(null, { status: 302, headers: { location: "/login", "set-cookie": `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax` } });
    }

    if (path === "/login") {
      if (request.method === "GET") return loginPage(url.searchParams.get("next") ?? "/");
      if (request.method === "POST") {
        const form = await request.formData();
        const given = String(form.get("password") ?? "");
        const nextPath = String(form.get("next") ?? "/");
        if (!env.DEMO_PASSWORD || !timingSafeEqual(given, env.DEMO_PASSWORD)) return loginPage(nextPath, "That password is not right.");
        return new Response(null, { status: 303, headers: { location: nextPath.startsWith("/") && !nextPath.startsWith("//") ? nextPath : "/", "set-cookie": await makeCookie(env) } });
      }
      return new Response("method not allowed", { status: 405 });
    }

    const authed = await hasValidCookie(request, env);
    if (path.startsWith("/api/")) {
      if (!authed) return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401, headers: { "content-type": "application/json" } });
      return proxyApi(request, env, url);
    }
    if (!authed) {
      return new Response(null, { status: 302, headers: { location: `/login?next=${encodeURIComponent(path + url.search)}` } });
    }
    const res = await next();
    const h = new Headers(res.headers);
    h.set("x-robots-tag", "noindex, nofollow");
    return new Response(res.body, { status: res.status, headers: h });
  },
];
