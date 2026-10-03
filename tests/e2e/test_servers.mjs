import assert from "node:assert/strict";
import { test } from "node:test";
import { chosen, err, harness, hooks, list, reset, row, said, settings, title, until } from "./server.mjs";

const h = harness();

const USER = { id: "1", username: "tester", global_name: "Tester" };
const GUILDS = { guilds: [{ id: "100", name: "Alpha", icon: "" }, { id: "200", name: "Beta", icon: "" }] };
const CHANNELS = {
  100: { channels: [{ id: "101", name: "general", parent: "", topic: "" }, { id: "102", name: "news", parent: "Info", topic: "" }] },
  200: { channels: [{ id: "201", name: "chat", parent: "", topic: "" }] },
};
const SERVERS = "enter toggles the server, right opens channels, esc back";
const CHANNEL = "enter toggles, a selects all, esc back";

function gate() {
  let open;
  const shut = new Promise((resolve) => (open = resolve));
  return { shut, open };
}

async function signed(route) {
  const response = await route.fetch();
  const body = await response.json();
  body.user = USER;
  return { response, json: body };
}

async function fixtures(page, { channels = CHANNELS, hold = null } = {}) {
  let puts = 0;
  await page.route("**/api/state", async (route) => route.fulfill(await signed(route)));
  await page.route("**/api/setup", async (route) => {
    puts += 1;
    const answer = await signed(route);
    if (hold && puts === 1) {
      hold.got();
      await hold.shut;
    }
    await route.fulfill(answer);
  });
  await page.route("**/api/guilds", (route) => route.fulfill({ json: GUILDS }));
  await page.route("**/api/guilds/*/channels", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    return route.fulfill({ json: channels[id] || { channels: [] } });
  });
}

function put(page) {
  return page.waitForResponse((r) => r.url().endsWith("/api/setup") && r.request().method() === "PUT");
}

async function picks() {
  const state = await h.srv.api("GET", "/api/state");
  return state.selection.filter((r) => r.enabled).map((r) => r.channel_id).sort();
}

async function servers(page) {
  await settings(page, h.srv.base);
  await said(page, "who", "Tester");
  await row(page, 1, "2  Select servers");
  await page.keyboard.press("2");
  await title(page, "Select servers");
  await list(page, ["[ ] Alpha", "[ ] Beta"]);
}

async function alpha(page) {
  await servers(page);
  await page.keyboard.press("ArrowRight");
  await title(page, "Alpha");
  await list(page, ["[ ] #general", "[ ] Info / #news"]);
}

test("the server list renders and enter toggles a whole server", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  const page = await h.page(t);
  await fixtures(page);
  await servers(page);
  await said(page, "hint", SERVERS);
  await chosen(page, "[ ] Alpha");
  let answer = put(page);
  await page.keyboard.press("Enter");
  assert.equal((await answer).status(), 200);
  await chosen(page, "[x] Alpha");
  await row(page, 1, "[ ] Beta");
  const state = await h.srv.api("GET", "/api/state");
  assert.deepEqual(state.selection.map((r) => [r.channel_id, r.guild_id, r.guild_name, r.enabled]).sort(), [
    ["101", "100", "Alpha", 1],
    ["102", "100", "Alpha", 1],
  ]);
  answer = put(page);
  await page.keyboard.press("Enter");
  await answer;
  await chosen(page, "[ ] Alpha");
  assert.deepEqual(await picks(), []);
  await page.keyboard.press("ArrowDown");
  await chosen(page, "[ ] Beta");
  answer = put(page);
  await page.keyboard.press("Enter");
  await answer;
  await list(page, ["[ ] Alpha", "[x] Beta"]);
  assert.deepEqual(await picks(), ["201"]);
  await page.keyboard.press("Escape");
  await title(page, "settings");
});

test("channels toggle one by one and all at once", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  const page = await h.page(t);
  await fixtures(page);
  await alpha(page);
  await said(page, "hint", CHANNEL);
  await chosen(page, "[ ] #general");
  let answer = put(page);
  await page.keyboard.press("Enter");
  await answer;
  await chosen(page, "[x] #general");
  assert.deepEqual(await picks(), ["101"]);
  await page.keyboard.press("ArrowDown");
  await chosen(page, "[ ] Info / #news");
  answer = put(page);
  await page.keyboard.press("Enter");
  await answer;
  await list(page, ["[x] #general", "[x] Info / #news"]);
  assert.deepEqual(await picks(), ["101", "102"]);
  for (const index of [1, 0]) {
    if (index === 0) await page.keyboard.press("ArrowUp");
    answer = put(page);
    await page.keyboard.press("Enter");
    await answer;
  }
  await list(page, ["[ ] #general", "[ ] Info / #news"]);
  assert.deepEqual(await picks(), []);
  answer = put(page);
  await page.keyboard.press("a");
  await answer;
  await list(page, ["[x] #general", "[x] Info / #news"]);
  assert.deepEqual(await picks(), ["101", "102"]);
  await page.keyboard.press("Escape");
  await title(page, "Select servers");
  await said(page, "hint", SERVERS);
  await list(page, ["[x] Alpha", "[ ] Beta"]);
  await chosen(page, "[x] Alpha");
  await page.keyboard.press("Escape");
  await title(page, "settings");
});

test("a server with nothing readable says so", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  const page = await h.page(t);
  await fixtures(page, { channels: { ...CHANNELS, 200: { channels: [] } } });
  await servers(page);
  await page.keyboard.press("ArrowDown");
  await chosen(page, "[ ] Beta");
  await page.keyboard.press("Enter");
  await err(page, "nothing in that server can be read");
  await page.keyboard.press("ArrowRight");
  await title(page, "Beta");
  await until(page, () => [...document.querySelectorAll("#screen .log")].some((e) => e.textContent === "nothing in that server can be read"));
  assert.deepEqual(await picks(), []);
  await page.keyboard.press("Escape");
  await title(page, "Select servers");
  await chosen(page, "[ ] Beta");
});

test("a late answer to an earlier toggle never undoes a later one", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  const page = await h.page(t);
  const hold = gate();
  const applied = gate();
  hold.got = applied.open;
  await fixtures(page, { hold });
  await alpha(page);
  const first = page.waitForRequest((r) => r.url().endsWith("/api/setup") && r.method() === "PUT");
  await page.keyboard.press("Enter");
  await first;
  await applied.shut;
  assert.deepEqual(await picks(), ["101"]);
  await page.keyboard.press("ArrowDown");
  await chosen(page, "[ ] Info / #news");
  await page.keyboard.press("Enter");
  await page.evaluate(() => {
    window.seen = [];
    new MutationObserver(() => {
      window.seen.push([...document.querySelectorAll("#screen .row")].map((e) => e.textContent));
    }).observe(document.getElementById("screen"), { childList: true });
  });
  const second = page.waitForRequest((r) => r.url().endsWith("/api/setup") && r.method() === "PUT");
  hold.open();
  const sent = await second;
  assert.deepEqual(sent.postDataJSON().channels.map((r) => r.channel_id).sort(), ["101", "102"]);
  assert.equal((await sent.response()).status(), 200);
  await list(page, ["[x] #general", "[x] Info / #news"]);
  const seen = await page.evaluate(() => window.seen);
  assert.ok(seen.length > 0);
  for (const shown of seen) assert.deepEqual(shown, ["[x] #general", "[x] Info / #news"]);
  assert.deepEqual(await picks(), ["101", "102"]);
});

test("a state refetch that started before a save never undoes it", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  const stale = JSON.stringify(await h.srv.api("GET", "/api/state"));
  const page = await h.page(t);
  const stream = gate();
  const save = gate();
  let streams = 0;
  let states = 0;
  let caught = null;
  const held = gate();
  await page.route("**/api/events", async (route) => {
    streams += 1;
    if (streams > 1) return route.continue();
    await stream.shut;
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: "retry: 20\n\n" });
  });
  await page.route("**/api/setup", async (route) => {
    await save.shut;
    await route.continue();
  });
  await page.route("**/api/state", async (route) => {
    states += 1;
    if (states === 1) return route.continue();
    caught = route;
    held.open();
  });
  await hooks(page, h.srv.base);
  await row(page, 2, "3  threads  off");
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/setup") && r.method() === "PUT");
  await page.keyboard.press("3");
  await sent;
  stream.open();
  await held.shut;
  save.open();
  await row(page, 2, "3  threads  on");
  await caught.fulfill({ status: 200, contentType: "application/json", body: stale });
  const flipped = await page
    .waitForFunction(() => document.querySelectorAll("#screen .row")[2].textContent !== "3  threads  on", null, { timeout: 300, polling: 20 })
    .then(() => true, () => false);
  assert.equal(flipped, false);
  await row(page, 2, "3  threads  on");
  assert.equal((await h.srv.api("GET", "/api/state")).options.include_threads, true);
});
