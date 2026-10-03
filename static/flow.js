const MENU = [
  { id: "start", label: "Start/Resume mirror" },
  { id: "settings", label: "Settings" },
  { id: "exit", label: "Exit" },
];

const KNOWN = new Set(["welcome", "menu", "settings", "token", "webhooks", "servers", "exit"]);

export function items(screen, ctx) {
  const tokenOk = !!(ctx && ctx.tokenOk);
  if (screen === "menu") return MENU.map((item) => ({ id: item.id, label: item.label }));
  if (screen === "settings") {
    return [
      { id: "token", label: "Add token" },
      { id: "servers", label: "Select servers", locked: !tokenOk },
      { id: "webhooks", label: "Webhook settings" },
      { id: "back", label: "Back to menu" },
    ];
  }
  return [];
}

function pack(screen, index, action, locked) {
  return { screen, index, action, locked };
}

function activate(item, index) {
  if (item.id === "start") return pack("menu", index, "start", false);
  if (item.id === "settings") return pack("settings", 0, "settings", false);
  if (item.id === "exit") return pack("exit", 0, "exit", false);
  if (item.id === "token") return pack("token", 0, "token", false);
  if (item.id === "servers") return pack("servers", 0, "servers", false);
  if (item.id === "webhooks") return pack("webhooks", 0, "webhooks", false);
  if (item.id === "back") return pack("menu", 0, "back", false);
  return pack("menu", index, item.id, false);
}

export function reduce(state, event, ctx) {
  if (state == null) return pack("welcome", 0, null, false);
  let screen = state.screen;
  let index = state.index;
  if (!KNOWN.has(screen)) {
    screen = "welcome";
    index = 0;
  }
  const key = event && event.key;
  if (screen === "welcome") {
    if (key === "Escape") return pack("welcome", index, null, false);
    return pack("menu", 0, null, false);
  }
  if (key === "Escape") {
    if (screen === "token" || screen === "webhooks" || screen === "servers") return pack("settings", 0, null, false);
    if (screen === "settings" || screen === "exit") return pack("menu", 0, null, false);
    return pack(screen, index, null, false);
  }
  if (screen === "token" || screen === "webhooks" || screen === "servers" || screen === "exit") {
    return pack(screen, index, null, false);
  }
  const list = items(screen, ctx);
  const count = list.length;
  if (key === "ArrowDown") return pack(screen, (index + 1) % count, null, false);
  if (key === "ArrowUp") return pack(screen, (index - 1 + count) % count, null, false);
  if (typeof key === "string" && key.length === 1 && key >= "1" && key <= "9") {
    const pick = Number(key) - 1;
    if (pick >= count) return pack(screen, index, null, false);
    const item = list[pick];
    if (item.locked) return pack(screen, pick, null, true);
    return activate(item, pick);
  }
  if (key === "Enter") {
    const item = list[index];
    if (!item) return pack(screen, index, null, false);
    if (item.locked) return pack(screen, index, null, true);
    return activate(item, index);
  }
  return pack(screen, index, null, false);
}
