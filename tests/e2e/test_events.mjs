import assert from "node:assert/strict";
import { test } from "node:test";
import { harness, hooks, reset, row, said, title, until } from "./server.mjs";

const h = harness();

function gate() {
  let open;
  const shut = new Promise((resolve) => (open = resolve));
  return { shut, open };
}

function asked(page, tail, ms) {
  return page.waitForRequest((r) => r.url().endsWith(tail) && r.method() === "GET", ms ? { timeout: ms } : undefined);
}

async function idle() {
  const state = await h.srv.api("DELETE", "/api/session");
  assert.equal(state.status, "idle");
}

async function live(page) {
  assert.notEqual(await page.textContent("#status"), "stopped");
  const seen = said(page, "status", "stopped");
  await h.srv.api("POST", "/api/stop");
  await seen;
}

test("an ended stream reconnects and refetches state and feed", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  await idle();
  const page = await h.page(t);
  const stream = gate();
  let streams = 0;
  await page.route("**/api/events", async (route) => {
    streams += 1;
    if (streams > 1) return route.continue();
    await stream.shut;
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: "retry: 50\n\n" });
  });
  await hooks(page, h.srv.base);
  await row(page, 0, "1  server name  mirror");
  await reset(h.srv.api, { dest_name: "gamma" });
  const again = asked(page, "/api/events");
  const state = asked(page, "/api/state");
  const feed = asked(page, "/api/feed");
  stream.open();
  await Promise.all([again, state, feed]);
  await row(page, 0, "1  server name  gamma");
  await title(page, "Webhook settings");
  await live(page);
});

test("a failed stream is opened again by the page", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  await idle();
  const page = await h.page(t);
  let streams = 0;
  await page.route("**/api/events", async (route) => {
    streams += 1;
    if (streams > 1) return route.continue();
    await route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
  });
  let seen = 0;
  const again = page.waitForResponse((r) => r.url().endsWith("/api/events") && ++seen === 2, { timeout: 6000 });
  await page.goto(h.srv.base + "/");
  await page.waitForSelector(".welcome");
  assert.equal((await again).status(), 200);
  await said(page, "status", "idle");
  await live(page);
  assert.ok(streams >= 2);
});

test("a failed first load recovers when the stream opens", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api, { dest_name: "alpha" });
  const page = await h.page(t);
  let states = 0;
  await page.route("**/api/state", async (route) => {
    states += 1;
    if (states === 1) return route.abort();
    await route.continue();
  });
  await hooks(page, h.srv.base);
  await row(page, 0, "1  server name  alpha");
  await until(page, () => !document.querySelector("#screen .err"));
  assert.equal(states, 2);
  const answer = page.waitForResponse((r) => r.url().endsWith("/api/setup") && r.request().method() === "PUT");
  await page.keyboard.press("3");
  assert.equal((await answer).status(), 200);
  await row(page, 2, "3  threads  on");
  const saved = (await h.srv.api("GET", "/api/state")).options;
  assert.equal(saved.dest_name, "alpha");
  assert.equal(saved.include_threads, true);
});
