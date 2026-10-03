import assert from "node:assert/strict";
import { test } from "node:test";
import { chosen, click, harness, list, open, said, title } from "./server.mjs";

const h = harness();
const MENU = ["1  Start/Resume mirror", "2  Settings", "3  Exit"];

test("welcome screen opens the menu on any key", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await open(page, h.srv.base);
  assert.equal(await page.textContent(".welcome h2"), "Discord smart scraper - Mando");
  await said(page, "hint", "press any key");
  await said(page, "who", "signed out");
  await said(page, "status", "idle");
  await page.keyboard.press("x");
  await title(page, "menu");
  await list(page, MENU);
  await chosen(page, MENU[0]);
  await said(page, "hint", "up and down move, enter or a number opens, esc back");
});

test("menu moves with arrows and opens with numbers, enter and clicks", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await open(page, h.srv.base);
  await page.keyboard.press("Enter");
  await title(page, "menu");
  await page.keyboard.press("ArrowDown");
  await chosen(page, MENU[1]);
  await page.keyboard.press("ArrowDown");
  await chosen(page, MENU[2]);
  await page.keyboard.press("ArrowDown");
  await chosen(page, MENU[0]);
  await page.keyboard.press("ArrowUp");
  await chosen(page, MENU[2]);
  await page.keyboard.press("2");
  await title(page, "settings");
  await page.keyboard.press("Escape");
  await title(page, "menu");
  await chosen(page, MENU[0]);
  await page.keyboard.press("ArrowDown");
  await chosen(page, MENU[1]);
  await page.keyboard.press("Enter");
  await title(page, "settings");
  await page.keyboard.press("Escape");
  await title(page, "menu");
  await click(page, 1);
  await title(page, "settings");
  await page.keyboard.press("Escape");
  await title(page, "menu");
});

test("exit shows stopped and any key returns to the menu", { timeout: 20000 }, async (t) => {
  const page = await h.page(t);
  await open(page, h.srv.base);
  await page.keyboard.press("Enter");
  await title(page, "menu");
  await click(page, 2);
  await title(page, "Stopped");
  assert.equal(await page.textContent("#screen h2.title"), "Stopped");
  await said(page, "hint", "press any key");
  await said(page, "status", "stopped");
  await page.keyboard.press("x");
  await title(page, "menu");
  await chosen(page, MENU[0]);
  await page.keyboard.press("3");
  await title(page, "Stopped");
  await said(page, "hint", "press any key");
  await page.keyboard.press("Enter");
  await title(page, "menu");
  await chosen(page, MENU[0]);
  await list(page, MENU);
});
