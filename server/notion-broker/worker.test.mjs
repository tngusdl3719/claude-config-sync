// node --test server/notion-broker/  (Node 18+: fetch, Request and Response are built in)
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import worker from "./worker.js";

const env = {
  NOTION_CLIENT_ID: "client-1",
  NOTION_CLIENT_SECRET: "secret-1",
  REDIRECT_URI: "http://localhost:47821/callback",
};
const realFetch = globalThis.fetch;
let sent;

function notionAnswers(status, body) {
  sent = [];
  globalThis.fetch = async (url, init) => {
    sent.push({ url: String(url), init });
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  };
}

function post(body, { path = "/token", type = "application/json", method = "POST" } = {}) {
  return worker.fetch(new Request("https://broker.example" + path, {
    method,
    headers: { "Content-Type": type },
    body: method === "GET" ? undefined : typeof body === "string" ? body : JSON.stringify(body),
  }), env);
}

afterEach(() => { globalThis.fetch = realFetch; });

test("a code goes to Notion with the secret, only the fields it needs, and the answer comes back", async () => {
  notionAnswers(200, { access_token: "ntn_a", refresh_token: "nrt_a", workspace_name: "w" });
  const r = await post({ grant_type: "authorization_code", code: "c1", redirect_uri: env.REDIRECT_URI, extra: "x" });
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { access_token: "ntn_a", refresh_token: "nrt_a", workspace_name: "w" });
  assert.equal(r.headers.get("Cache-Control"), "no-store");
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, "https://api.notion.com/v1/oauth/token");
  assert.equal(sent[0].init.method, "POST");
  assert.equal(sent[0].init.headers.Authorization, "Basic " + btoa("client-1:secret-1"));
  assert.deepEqual(JSON.parse(sent[0].init.body), { grant_type: "authorization_code", code: "c1", redirect_uri: env.REDIRECT_URI });
});

test("a refresh token is passed on the same way", async () => {
  notionAnswers(200, { access_token: "ntn_b", refresh_token: "nrt_b" });
  const r = await post({ grant_type: "refresh_token", refresh_token: "nrt_a" });
  assert.equal(r.status, 200);
  assert.deepEqual(JSON.parse(sent[0].init.body), { grant_type: "refresh_token", refresh_token: "nrt_a" });
});

test("Notion's refusal comes back as it was", async () => {
  notionAnswers(400, { error: "invalid_grant" });
  const r = await post({ grant_type: "refresh_token", refresh_token: "old" });
  assert.equal(r.status, 400);
  assert.deepEqual(await r.json(), { error: "invalid_grant" });
});

test("anything else is refused before Notion is asked", async () => {
  notionAnswers(200, {});
  const cases = [
    [{ grant_type: "authorization_code", code: "c1", redirect_uri: "https://evil.example/cb" }, {}, 400],
    [{ grant_type: "authorization_code", redirect_uri: env.REDIRECT_URI }, {}, 400],
    [{ grant_type: "refresh_token" }, {}, 400],
    [{ grant_type: "client_credentials" }, {}, 400],
    [{ grant_type: "refresh_token", refresh_token: 5 }, {}, 400],
    ["not json", {}, 400],
    [[1, 2], {}, 400],
    [{ grant_type: "refresh_token", refresh_token: "x".repeat(5000) }, {}, 413],
    [{ grant_type: "refresh_token", refresh_token: "r" }, { type: "text/plain" }, 415],
    [{ grant_type: "refresh_token", refresh_token: "r" }, { path: "/other" }, 404],
    [null, { method: "GET" }, 405],
  ];
  for (const [body, opts, status] of cases) {
    const r = await post(body, opts);
    assert.equal(r.status, status, JSON.stringify([body, opts]));
    assert.equal(r.headers.get("Cache-Control"), "no-store");
  }
  assert.equal(sent.length, 0);
});

test("without its secret the broker says so instead of asking Notion", async () => {
  notionAnswers(200, {});
  const r = await worker.fetch(new Request("https://broker.example/token", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ grant_type: "refresh_token", refresh_token: "r" }),
  }), { ...env, NOTION_CLIENT_SECRET: "" });
  assert.equal(r.status, 500);
  assert.equal(sent.length, 0);
});

test("Notion out of reach is a 502, with nothing of the request in it", async () => {
  globalThis.fetch = async () => { throw new Error("connect failed secret-1"); };
  const r = await post({ grant_type: "refresh_token", refresh_token: "nrt_a" });
  assert.equal(r.status, 502);
  const text = await r.text();
  assert.ok(!text.includes("secret-1") && !text.includes("nrt_a"), text);
});

test("a big body with no length given stops being read as soon as it is too big", async () => {
  notionAnswers(200, {});
  let pulled = 0;
  const chunk = new Uint8Array(1024).fill(32);
  const body = new ReadableStream({
    pull(controller) {
      pulled += chunk.byteLength;
      if (pulled > 1024 * 1024) controller.close();
      else controller.enqueue(chunk);
    },
  });
  const r = await worker.fetch(new Request("https://broker.example/token", {
    method: "POST", headers: { "Content-Type": "application/json" }, body, duplex: "half",
  }), env);
  assert.equal(r.status, 413);
  assert.ok(pulled <= 8 * 1024, "read " + pulled + " bytes of a 1 MiB body");
  assert.equal(sent.length, 0);
});
