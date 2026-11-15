// The Notion sign-in broker for Claude Sync (a Cloudflare Worker). Notion's sign-in ends with a request
// that must carry the app's client secret; an app anyone may download cannot keep one, so that last step
// comes here. The secret lives only in this Worker (wrangler secret put NOTION_CLIENT_SECRET). It passes
// on a sign-in code (for the app's own localhost redirect only) or a refresh token, and hands Notion's
// answer straight back. It keeps nothing and logs nothing.

const NOTION_TOKEN = "https://api.notion.com/v1/oauth/token";
const MAX_BODY = 4096;

/** The body as text, read only up to MAX_BODY bytes: null as soon as it is bigger (and the rest unread). */
async function readLimited(request) {
  if (!request.body) return "";
  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, at);
    at += chunk.byteLength;
  }
  return new TextDecoder().decode(bytes);
}

function reply(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

/** Only the fields Notion needs, and only for what the app does: a sign-in code or a refresh. */
function tokenRequest(input, env) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  if (input.grant_type === "authorization_code") {
    if (typeof input.code !== "string" || !input.code || input.redirect_uri !== env.REDIRECT_URI) return null;
    return { grant_type: "authorization_code", code: input.code, redirect_uri: input.redirect_uri };
  }
  if (input.grant_type === "refresh_token") {
    if (typeof input.refresh_token !== "string" || !input.refresh_token) return null;
    return { grant_type: "refresh_token", refresh_token: input.refresh_token };
  }
  return null;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/token") return reply(404, { error: "not_found" });
    if (request.method !== "POST") return reply(405, { error: "method_not_allowed" });
    if (!(request.headers.get("Content-Type") || "").startsWith("application/json")) {
      return reply(415, { error: "invalid_request" });
    }
    if (Number(request.headers.get("Content-Length") || 0) > MAX_BODY) return reply(413, { error: "invalid_request" });
    const text = await readLimited(request);
    if (text === null) return reply(413, { error: "invalid_request" });
    let input;
    try {
      input = JSON.parse(text);
    } catch {
      return reply(400, { error: "invalid_request" });
    }
    const body = tokenRequest(input, env);
    if (!body) return reply(400, { error: "invalid_request" });
    if (!env.NOTION_CLIENT_ID || !env.NOTION_CLIENT_SECRET) return reply(500, { error: "server_error" });

    let answer;
    try {
      answer = await fetch(NOTION_TOKEN, {
        method: "POST",
        headers: {
          Authorization: "Basic " + btoa(env.NOTION_CLIENT_ID + ":" + env.NOTION_CLIENT_SECRET),
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(body),
      });
    } catch {
      return reply(502, { error: "temporarily_unavailable" });
    }
    return new Response(await answer.text(), {
      status: answer.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  },
};
