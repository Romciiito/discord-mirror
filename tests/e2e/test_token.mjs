import assert from "node:assert/strict";
import { test } from "node:test";
import { chosen, click, harness, row, said, token, until } from "./server.mjs";

const h = harness();
const EDIT = "enter keeps it, esc cancels the edit";
const TOKEN_EDIT = "enter or esc keeps it";
const IDLE = "up and down move, enter opens, esc back";
const TEN = "abcdefghij";
const PASTED = "fake-token_for.tests_only-0123456789.abcdefghij_klmnop";

function editing(page, label) {
  return until(page, (want) => {
    const input = document.querySelector("#screen .row input");
    return !!input && input.parentElement.firstChild.textContent === want;
  }, label);
}

function closed(page) {
  return until(page, () => !document.querySelector("#screen .row input"));
}

async function paste(page, value) {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], { origin: h.srv.base });
  await page.evaluate((text) => navigator.clipboard.writeText(text), value);
  await page.keyboard.press("ControlOrMeta+V");
}

test("clicking the row being edited keeps the draft", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await token(page, h.srv.base);
  await page.keyboard.press("4");
  await editing(page, "keychain service ");
  await page.keyboard.type("svc");
  await click(page, 3);
  await editing(page, "keychain service ");
  assert.equal(await page.inputValue("#screen .row input"), "svc");
  await said(page, "hint", EDIT);
});

test("clicking another row keeps the draft and opens that row", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await token(page, h.srv.base);
  await page.keyboard.press("1");
  await editing(page, "token ");
  await page.keyboard.type(TEN);
  await click(page, 3);
  await row(page, 0, "1  token  set");
  await editing(page, "keychain service ");
  await page.keyboard.type("svc");
  await click(page, 4);
  await row(page, 3, "4  keychain service  svc");
  await editing(page, "keychain account ");
  await page.keyboard.type("acc");
  await click(page, 1);
  await closed(page);
  await row(page, 4, "5  keychain account  acc");
  await row(page, 1, "2  keep on this machine  no");
  await page.keyboard.press("1");
  await editing(page, "token ");
  assert.equal(await page.inputValue("#screen .row input"), TEN);
});

test("escape restores and enter keeps the keychain fields", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await token(page, h.srv.base);
  await said(page, "hint", IDLE);
  for (const [key, index, label] of [["4", 3, "keychain service"], ["5", 4, "keychain account"]]) {
    await page.keyboard.press(key);
    await editing(page, label + " ");
    await said(page, "hint", EDIT);
    assert.equal(await page.inputValue("#screen .row input"), "");
    await page.keyboard.type("svc");
    await page.keyboard.press("Enter");
    await closed(page);
    await row(page, index, key + "  " + label + "  svc");
    await said(page, "hint", IDLE);
    await page.keyboard.press(key);
    await editing(page, label + " ");
    assert.equal(await page.inputValue("#screen .row input"), "svc");
    await page.keyboard.type("x");
    assert.equal(await page.inputValue("#screen .row input"), "svcx");
    await page.keyboard.press("Escape");
    await closed(page);
    await row(page, index, key + "  " + label + "  svc");
    await said(page, "hint", IDLE);
    await page.keyboard.press(key);
    await editing(page, label + " ");
    assert.equal(await page.inputValue("#screen .row input"), "svc");
    await page.keyboard.press("Escape");
    await closed(page);
  }
});

test("escape and enter keep the token", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await token(page, h.srv.base);
  await row(page, 0, "1  token");
  await page.keyboard.press("1");
  await editing(page, "token ");
  await said(page, "hint", TOKEN_EDIT);
  assert.equal(await page.getAttribute("#screen .row input", "type"), "password");
  await paste(page, PASTED);
  await page.keyboard.press("Escape");
  await closed(page);
  await row(page, 0, "1  token  set");
  await page.keyboard.press("1");
  await editing(page, "token ");
  assert.equal(await page.inputValue("#screen .row input"), PASTED);
  await page.locator("#screen .row input").fill(TEN);
  await page.click("#hint");
  await page.keyboard.press("Escape");
  await closed(page);
  await page.keyboard.press("1");
  await editing(page, "token ");
  assert.equal(await page.inputValue("#screen .row input"), TEN);
  await page.locator("#screen .row input").fill("zzz");
  await page.keyboard.press("Enter");
  await row(page, 0, "1  token  set");
  await page.keyboard.press("1");
  await editing(page, "token ");
  assert.equal(await page.inputValue("#screen .row input"), "zzz");
});

test("typing on the closed token row opens it with the text", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await token(page, h.srv.base);
  await chosen(page, "1  token");
  await page.keyboard.type("MTIz.Gx_y-Z");
  await editing(page, "token ");
  assert.equal(await page.inputValue("#screen .row input"), "MTIz.Gx_y-Z");
});

test("pasting on the closed token row opens it with the text", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await token(page, h.srv.base);
  await chosen(page, "1  token");
  await paste(page, PASTED);
  await editing(page, "token ");
  assert.equal(await page.inputValue("#screen .row input"), PASTED);
});

test("keep on this machine toggles", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await token(page, h.srv.base);
  await row(page, 1, "2  keep on this machine  yes");
  await page.keyboard.press("2");
  await row(page, 1, "2  keep on this machine  no");
  await page.keyboard.press("Enter");
  await row(page, 1, "2  keep on this machine  yes");
  await click(page, 1);
  await row(page, 1, "2  keep on this machine  no");
});
