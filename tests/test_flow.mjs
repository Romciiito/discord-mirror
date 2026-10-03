import test from "node:test";
import assert from "node:assert/strict";
import { items, reduce } from "../static/flow.js";

const welcome = { screen: "welcome", index: 0, action: null, locked: false };

test("null state opens welcome", () => {
  assert.deepEqual(reduce(null, { key: "Enter" }, { tokenOk: true }), welcome);
  assert.deepEqual(reduce(null), welcome);
});

test("any key leaves welcome for the menu", () => {
  const menu = { screen: "menu", index: 0, action: null, locked: false };
  for (const key of ["Enter", "a", "ArrowDown", "1", "Escape"]) {
    assert.deepEqual(reduce({ screen: "welcome", index: 0 }, { key }), key === "Escape" ? welcome : menu);
  }
});

test("menu labels and order", () => {
  assert.deepEqual(items("menu"), [
    { id: "start", label: "Start/Resume mirror" },
    { id: "settings", label: "Settings" },
    { id: "exit", label: "Exit" },
  ]);
  assert.deepEqual(items("menu", { tokenOk: true }), items("menu"));
});

test("settings item 2 is locked only when tokenOk is false", () => {
  const locked = [
    { id: "token", label: "Add token" },
    { id: "servers", label: "Select servers", locked: true },
    { id: "webhooks", label: "Webhook settings" },
    { id: "back", label: "Back to menu" },
  ];
  const open = [
    { id: "token", label: "Add token" },
    { id: "servers", label: "Select servers", locked: false },
    { id: "webhooks", label: "Webhook settings" },
    { id: "back", label: "Back to menu" },
  ];
  assert.deepEqual(items("settings", { tokenOk: false }), locked);
  assert.deepEqual(items("settings"), locked);
  assert.deepEqual(items("settings", { tokenOk: true }), open);
  assert.equal(items("welcome").length, 0);
  assert.equal(items("token", { tokenOk: true }).length, 0);
  assert.equal(items("webhooks").length, 0);
  assert.equal(items("servers", { tokenOk: true }).length, 0);
  assert.equal(items("exit").length, 0);
});

test("digit 2 on settings with no token does not open servers", () => {
  const state = { screen: "settings", index: 0 };
  assert.deepEqual(reduce(state, { key: "2" }, { tokenOk: false }), {
    screen: "settings",
    index: 1,
    action: null,
    locked: true,
  });
  assert.deepEqual(reduce(state, { key: "2" }), {
    screen: "settings",
    index: 1,
    action: null,
    locked: true,
  });
  assert.deepEqual(reduce({ screen: "settings", index: 1 }, { key: "Enter" }), {
    screen: "settings",
    index: 1,
    action: null,
    locked: true,
  });
  assert.deepEqual(state, { screen: "settings", index: 0 });
});

test("digit or enter on unlocked servers opens servers", () => {
  assert.deepEqual(reduce({ screen: "settings", index: 0 }, { key: "2" }, { tokenOk: true }), {
    screen: "servers",
    index: 0,
    action: "servers",
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "settings", index: 1 }, { key: "Enter" }, { tokenOk: true }), {
    screen: "servers",
    index: 0,
    action: "servers",
    locked: false,
  });
});

test("escape from servers returns to settings", () => {
  assert.deepEqual(reduce({ screen: "servers", index: 2 }, { key: "Escape" }, { tokenOk: true }), {
    screen: "settings",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "servers", index: 0 }, { key: "Enter" }, { tokenOk: true }), {
    screen: "servers",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "servers", index: 0 }, { key: "ArrowDown" }, { tokenOk: true }), {
    screen: "servers",
    index: 0,
    action: null,
    locked: false,
  });
});

test("start action", () => {
  const state = { screen: "menu", index: 0 };
  const next = reduce(state, { key: "Enter" });
  assert.deepEqual(next, { screen: "menu", index: 0, action: "start", locked: false });
  assert.notEqual(next, state);
  assert.deepEqual(reduce({ screen: "menu", index: 2 }, { key: "1" }), {
    screen: "menu",
    index: 0,
    action: "start",
    locked: false,
  });
});

test("exit screen", () => {
  assert.deepEqual(reduce({ screen: "menu", index: 2 }, { key: "Enter" }), {
    screen: "exit",
    index: 0,
    action: "exit",
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "menu", index: 0 }, { key: "3" }), {
    screen: "exit",
    index: 0,
    action: "exit",
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "exit", index: 0 }, { key: "Escape" }), {
    screen: "menu",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "exit", index: 0 }, { key: "Enter" }), {
    screen: "exit",
    index: 0,
    action: null,
    locked: false,
  });
});

test("wrap from last item to first", () => {
  assert.deepEqual(reduce({ screen: "menu", index: 2 }, { key: "ArrowDown" }), {
    screen: "menu",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "menu", index: 0 }, { key: "ArrowUp" }), {
    screen: "menu",
    index: 2,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "settings", index: 3 }, { key: "ArrowDown" }, { tokenOk: false }), {
    screen: "settings",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "settings", index: 0 }, { key: "ArrowUp" }, { tokenOk: true }), {
    screen: "settings",
    index: 3,
    action: null,
    locked: false,
  });
});

test("digit 9 ignored", () => {
  assert.deepEqual(reduce({ screen: "menu", index: 1 }, { key: "9" }), {
    screen: "menu",
    index: 1,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "settings", index: 2 }, { key: "9" }, { tokenOk: true }), {
    screen: "settings",
    index: 2,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "menu", index: 0 }, { key: "4" }), {
    screen: "menu",
    index: 0,
    action: null,
    locked: false,
  });
});

test("navigation and form keys", () => {
  assert.deepEqual(reduce({ screen: "menu", index: 1 }, { key: "Enter" }), {
    screen: "settings",
    index: 0,
    action: "settings",
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "menu", index: 0 }, { key: "2" }), {
    screen: "settings",
    index: 0,
    action: "settings",
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "settings", index: 0 }, { key: "Enter" }), {
    screen: "token",
    index: 0,
    action: "token",
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "settings", index: 0 }, { key: "3" }, { tokenOk: false }), {
    screen: "webhooks",
    index: 0,
    action: "webhooks",
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "settings", index: 3 }, { key: "Enter" }, { tokenOk: true }), {
    screen: "menu",
    index: 0,
    action: "back",
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "settings", index: 1 }, { key: "4" }), {
    screen: "menu",
    index: 0,
    action: "back",
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "token", index: 0 }, { key: "ArrowUp" }), {
    screen: "token",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "token", index: 0 }, { key: "1" }), {
    screen: "token",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "token", index: 0 }, { key: "Enter" }), {
    screen: "token",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "webhooks", index: 4 }, { key: "Escape" }), {
    screen: "settings",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "webhooks", index: 0 }, { key: "ArrowDown" }), {
    screen: "webhooks",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "settings", index: 2 }, { key: "Escape" }, { tokenOk: true }), {
    screen: "menu",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "menu", index: 1 }, { key: "Escape" }), {
    screen: "menu",
    index: 1,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "welcome", index: 0 }, { key: "Escape" }), welcome);
  assert.deepEqual(reduce({ screen: "nope", index: 4 }, { key: "x" }), {
    screen: "menu",
    index: 0,
    action: null,
    locked: false,
  });
  assert.deepEqual(reduce({ screen: "nope", index: 4 }, { key: "Escape" }), welcome);
  assert.deepEqual(reduce({ screen: "menu", index: 1 }, { key: "a" }), {
    screen: "menu",
    index: 1,
    action: null,
    locked: false,
  });
  const state = { screen: "menu", index: 1 };
  reduce(state, { key: "ArrowDown" });
  assert.deepEqual(state, { screen: "menu", index: 1 });
});
