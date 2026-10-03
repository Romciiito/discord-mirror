import assert from "node:assert/strict";
import { test } from "node:test";
import { chosen, click, harness, hooks, reset, row, said, title, until } from "./server.mjs";

const h = harness();
const EDIT = "enter keeps it, esc cancels the edit";
const IDLE = "up and down move, enter or a number opens, left and right change backfill and threads, esc back";

function put(page) {
  return page.waitForResponse((r) => r.url().endsWith("/api/setup") && r.request().method() === "PUT");
}

function editing(page) {
  return until(page, () => {
    const input = document.querySelector("#screen .row input");
    return !!input && input.parentElement.firstChild.textContent === "server name ";
  });
}

function closed(page) {
  return until(page, () => !document.querySelector("#screen .row input"));
}

async function options() {
  return (await h.srv.api("GET", "/api/state")).options;
}

async function back(page) {
  await page.reload();
  await page.waitForSelector(".welcome");
  await page.keyboard.press("Enter");
  await title(page, "menu");
  await page.keyboard.press("2");
  await title(page, "settings");
  await page.keyboard.press("3");
  await title(page, "Webhook settings");
}

test("clicking another row saves the name being typed", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  const page = await h.page(t);
  await hooks(page, h.srv.base);
  await page.keyboard.press("1");
  await editing(page);
  await page.locator("#screen .row input").fill("gamma");
  await click(page, 1);
  await closed(page);
  await row(page, 0, "1  server name  gamma");
  await row(page, 1, "2  backfill  last 25");
  const saved = await options();
  assert.equal(saved.dest_name, "gamma");
  assert.equal(saved.backfill, 25);
});

test("escape cancels the server name", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  const page = await h.page(t);
  await hooks(page, h.srv.base);
  await said(page, "hint", IDLE);
  await page.keyboard.press("1");
  await editing(page);
  await said(page, "hint", EDIT);
  assert.equal(await page.inputValue("#screen .row input"), "mirror");
  await page.locator("#screen .row input").fill("nope");
  await page.keyboard.press("Escape");
  await closed(page);
  await row(page, 0, "1  server name  mirror");
  await said(page, "hint", IDLE);
  assert.equal((await options()).dest_name, "mirror");
  await back(page);
  await row(page, 0, "1  server name  mirror");
  assert.equal((await options()).dest_name, "mirror");
});

test("enter saves the server name", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  const page = await h.page(t);
  await hooks(page, h.srv.base);
  await page.keyboard.press("1");
  await editing(page);
  await page.locator("#screen .row input").fill("beta");
  const answer = put(page);
  await page.keyboard.press("Enter");
  assert.equal((await answer).status(), 200);
  await row(page, 0, "1  server name  beta");
  await said(page, "hint", IDLE);
  assert.equal((await options()).dest_name, "beta");
  await back(page);
  await row(page, 0, "1  server name  beta");
  await page.keyboard.press("1");
  await editing(page);
  assert.equal(await page.inputValue("#screen .row input"), "beta");
});

test("backfill steps with enter, number, click and arrows", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  const page = await h.page(t);
  await hooks(page, h.srv.base);
  await page.keyboard.press("ArrowDown");
  await chosen(page, "2  backfill  live only");
  const steps = [
    [() => page.keyboard.press("Enter"), 25],
    [() => page.keyboard.press("2"), 50],
    [() => click(page, 1), 100],
    [() => page.keyboard.press("ArrowRight"), 250],
    [() => page.keyboard.press("ArrowRight"), 500],
    [() => page.keyboard.press("Enter"), 0],
    [() => page.keyboard.press("ArrowRight"), 25],
    [() => page.keyboard.press("ArrowLeft"), 0],
    [() => page.keyboard.press("ArrowLeft"), 0],
  ];
  for (const [act, amount] of steps) {
    const answer = put(page);
    await act();
    assert.equal((await answer).status(), 200);
    await chosen(page, "2  backfill  " + (amount ? "last " + amount : "live only"));
    assert.equal((await options()).backfill, amount);
  }
  await page.keyboard.press("ArrowRight");
  await chosen(page, "2  backfill  last 25");
  await page.keyboard.press("ArrowRight");
  await chosen(page, "2  backfill  last 50");
  for (let i = 0; i < 5; i++) {
    const answer = put(page);
    await page.keyboard.press("ArrowRight");
    await answer;
  }
  await chosen(page, "2  backfill  last 500");
  assert.equal((await options()).backfill, 500);
});

test("threads toggle with enter, click and arrows", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api);
  const page = await h.page(t);
  await hooks(page, h.srv.base);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await chosen(page, "3  threads  off");
  const steps = [
    [() => page.keyboard.press("Enter"), true],
    [() => click(page, 2), false],
    [() => page.keyboard.press("ArrowRight"), true],
    [() => page.keyboard.press("ArrowRight"), true],
    [() => page.keyboard.press("ArrowLeft"), false],
    [() => page.keyboard.press("ArrowLeft"), false],
    [() => page.keyboard.press("3"), true],
  ];
  for (const [act, on] of steps) {
    const answer = put(page);
    await act();
    assert.equal((await answer).status(), 200);
    await chosen(page, "3  threads  " + (on ? "on" : "off"));
    assert.equal((await options()).include_threads, on);
  }
  await back(page);
  await row(page, 2, "3  threads  on");
});

test("a cancelled name is never saved by a later change", { timeout: 20000 }, async (t) => {
  await reset(h.srv.api, { dest_name: "kept" });
  const page = await h.page(t);
  await hooks(page, h.srv.base);
  await row(page, 0, "1  server name  kept");
  await page.keyboard.press("1");
  await editing(page);
  await page.locator("#screen .row input").fill("nope");
  await page.keyboard.press("Escape");
  await closed(page);
  await page.keyboard.press("ArrowDown");
  const answer = put(page);
  await page.keyboard.press("Enter");
  await answer;
  await chosen(page, "2  backfill  last 25");
  await row(page, 0, "1  server name  kept");
  const saved = await options();
  assert.equal(saved.dest_name, "kept");
  assert.equal(saved.backfill, 25);
});
