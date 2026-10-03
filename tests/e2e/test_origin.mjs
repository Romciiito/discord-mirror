import assert from "node:assert/strict";
import { test } from "node:test";
import { harness, open } from "./server.mjs";

const h = harness();

function post(page, url, mode) {
  return page.evaluate(
    ([where, how]) =>
      fetch(where, { method: "POST", mode: how }).then(
        (r) => ({ status: r.status, type: r.type }),
        (e) => ({ error: e.message }),
      ),
    [url, mode],
  );
}

test("a page on another origin cannot post to the api", { timeout: 20000 }, async (t) => {
  const { base, port, api } = h.srv;
  const page = await h.page(t);
  await open(page, "http://localhost:" + port);
  assert.equal(new URL(page.url()).hostname, "localhost");
  const url = base + "/api/stop";
  const answer = page.waitForResponse((r) => r.url() === url);
  assert.deepEqual(await post(page, url, "no-cors"), { status: 0, type: "opaque" });
  const response = await answer;
  assert.equal(response.status(), 403);
  assert.equal((await api("GET", "/api/state")).status, "idle");
  const failed = page.waitForEvent("requestfailed", (r) => r.url() === url && r.method() === "POST");
  const result = await post(page, url, "cors");
  assert.match(result.error || "", /Failed to fetch/);
  await failed;
  assert.equal((await api("GET", "/api/state")).status, "idle");
});

test("the same origin can post to the api", { timeout: 20000 }, async (t) => {
  const { base, api } = h.srv;
  const page = await h.page(t);
  await open(page, base);
  const url = base + "/api/stop";
  const answer = page.waitForResponse((r) => r.url() === url);
  assert.deepEqual(await post(page, url, "cors"), { status: 200, type: "basic" });
  assert.equal((await answer).status(), 200);
  assert.equal((await api("GET", "/api/state")).status, "stopped");
});
