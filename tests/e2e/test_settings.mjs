import assert from "node:assert/strict";
import { test } from "node:test";
import { chosen, click, err, harness, menu, row, said, settings, title, token, until } from "./server.mjs";

const h = harness();
const TOKEN_EDIT = "enter or esc keeps it";

test("select servers is locked without a token", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await settings(page, h.srv.base);
  await row(page, 1, "2  Select servers  locked");
  assert.equal(await page.getAttribute("#screen .row:nth-of-type(2)", "class"), "row locked");
  await page.keyboard.press("2");
  await err(page, "add a working token first");
  await title(page, "settings");
  await chosen(page, "2  Select servers  locked");
  await page.keyboard.press("ArrowUp");
  await chosen(page, "1  Add token");
  await until(page, () => !document.querySelector("#screen .err"));
  await page.keyboard.press("ArrowDown");
  await chosen(page, "2  Select servers  locked");
  await page.keyboard.press("Enter");
  await err(page, "add a working token first");
  await title(page, "settings");
  await page.keyboard.press("ArrowDown");
  await chosen(page, "3  Webhook settings");
  await click(page, 1);
  await err(page, "add a working token first");
  await title(page, "settings");
  await chosen(page, "2  Select servers  locked");
});

test("a short token shows the server error", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await token(page, h.srv.base);
  await page.keyboard.press("1");
  await until(page, () => !!document.querySelector("#screen .row input"));
  assert.equal(await page.getAttribute("#screen .row input", "type"), "password");
  await said(page, "hint", TOKEN_EDIT);
  await page.keyboard.type("abc");
  await page.keyboard.press("Enter");
  await row(page, 0, "1  token  set");
  const answer = page.waitForResponse((r) => r.url().endsWith("/api/session") && r.request().method() === "POST");
  await page.keyboard.press("3");
  assert.equal((await answer).status(), 400);
  await err(page, "token looks too short");
  await title(page, "Add token");
  assert.equal((await h.srv.api("GET", "/api/state")).user, null);
});

test("an empty token asks for a token or the keychain", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await token(page, h.srv.base);
  await page.keyboard.press("3");
  await err(page, "paste a token or use the keychain fields");
  await title(page, "Add token");
});

test("escape walks back from every settings screen", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await token(page, h.srv.base);
  await page.keyboard.press("Escape");
  await title(page, "settings");
  await page.keyboard.press("3");
  await title(page, "Webhook settings");
  await page.keyboard.press("Escape");
  await title(page, "settings");
  await page.keyboard.press("4");
  await title(page, "menu");
  await page.keyboard.press("Escape");
  await title(page, "menu");
  await page.keyboard.press("2");
  await title(page, "settings");
  await page.keyboard.press("Escape");
  await title(page, "menu");
});

test("start without a token shows the error and stays on the menu", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await menu(page, h.srv.base);
  await page.keyboard.press("Enter");
  await err(page, "add a token first");
  await title(page, "menu");
  await chosen(page, "1  Start/Resume mirror");
  assert.equal((await h.srv.api("GET", "/api/state")).running, false);
  await page.keyboard.press("1");
  await err(page, "add a token first");
  await title(page, "menu");
});
