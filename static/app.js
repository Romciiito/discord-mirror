import { items, reduce } from "./flow.js";

const BACKFILL = [0, 25, 50, 100, 250, 500];

const term = document.getElementById("term");
const screen = document.getElementById("screen");
const whoNode = document.getElementById("who");
const statusNode = document.getElementById("status");
const hintNode = document.getElementById("hint");

let flow = reduce(null);
let snap = {
  user: null,
  running: false,
  status: "idle",
  options: { backfill: 0, include_threads: false, dest_name: "mirror", mirror: false, global_webhook: "" },
  selection: [],
  log: [],
};
let picked = new Map();
let guilds = [];
let channelRows = [];
let activeGuild = null;
let localIndex = 0;
let hookIndex = 0;
let tokenIndex = 0;
let typing = null;
let draft = "";
let saveSeq = 0;
let saveDone = 0;
let pending = 0;
let saveChain = Promise.resolve();
let errorText = "";
let busy = false;
let depth = "guilds";
let messages = [];
let loaded = false;
let late = null;
let lateUsers = 0;
let opens = 0;
const fields = { token: "", service: "", account: "", keep: true };

function ctx() {
  return { tokenOk: !!(snap.user && snap.user.id) };
}

function text(tag, value, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? "" : String(value);
  return node;
}

async function api(path, options) {
  const response = await fetch(path, options);
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  if (!response.ok) throw new Error(body.error || "request failed");
  return body;
}

function syncPicked() {
  picked = new Map();
  (snap.selection || []).forEach((row) => {
    if (row.enabled) picked.set(row.channel_id, row);
  });
}

function apply(next) {
  snap = next;
  syncPicked();
  whoNode.textContent = next.user ? next.user.global_name || next.user.username || next.user.id : "signed out";
  statusNode.textContent = next.running ? next.status || "live" : next.status || "idle";
}

function backfillLabel(value) {
  const amount = Number(value) || 0;
  return amount ? "last " + amount : "live only";
}

function guildSelected(guildId) {
  for (const row of picked.values()) {
    if (row.guild_id === guildId) return true;
  }
  return false;
}

function hint() {
  if (typing === "token") return "enter or esc keeps it";
  if (typing) return "enter keeps it, esc cancels the edit";
  if (flow.screen === "welcome") return "press any key";
  if (flow.screen === "exit") return "press any key";
  if (flow.screen === "running") return "esc returns to the menu";
  if (flow.screen === "servers" && depth === "channels") return "enter toggles, a selects all, esc back";
  if (flow.screen === "servers") return "enter toggles the server, right opens channels, esc back";
  if (flow.screen === "webhooks") return "up and down move, enter or a number opens, left and right change backfill and threads, esc back";
  if (flow.screen === "token") return "up and down move, enter opens, esc back";
  return "up and down move, enter or a number opens, esc back";
}

function rowButton(label, on, locked, activate) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "row" + (on ? " on" : "") + (locked ? " locked" : "");
  if (on) row.dataset.selected = "true";
  row.textContent = label;
  row.addEventListener("click", activate);
  return row;
}

function render() {
  if (flow.screen === "webhooks") {
    if (typing && typing !== "name") typing = null;
  } else if (flow.screen === "token") {
    if (typing && typing !== "token" && typing !== "service" && typing !== "account") typing = null;
  } else {
    typing = null;
  }
  hintNode.textContent = hint();
  screen.replaceChildren();
  if (flow.screen === "welcome") {
    const block = document.createElement("div");
    block.className = "welcome";
    const title = document.createElement("h2");
    title.textContent = "Discord smart scraper - Mando";
    block.append(title, text("p", "Press any key to continue", "log"));
    screen.append(block);
    return;
  }
  if (flow.screen === "exit") {
    screen.append(text("h2", "Stopped", "title"));
    screen.append(text("p", "Press any key to continue", "log"));
    return;
  }
  if (flow.screen === "running") {
    screen.append(text("div", snap.running ? "mirroring" : "working", "title"));
    (snap.log || []).slice().reverse().forEach((line) => screen.append(text("p", line, "log")));
    messages.slice(-12).forEach((message) => {
      const line = [message.channel_name ? "#" + message.channel_name : "", message.author, message.content]
        .filter(Boolean)
        .join("  ");
      screen.append(text("p", line, "msg"));
    });
    if (errorText) screen.append(text("p", errorText, "err"));
    screen.scrollTop = screen.scrollHeight;
    return;
  }
  if (flow.screen === "menu" || flow.screen === "settings") {
    screen.append(text("div", flow.screen === "menu" ? "menu" : "settings", "title"));
    items(flow.screen, ctx()).forEach((item, index) => {
      const locked = !!item.locked;
      const label = index + 1 + "  " + item.label + (locked ? "  locked" : "");
      screen.append(
        rowButton(label, index === flow.index, locked, () => {
          flow = { ...flow, index };
          press("Enter");
        }),
      );
    });
    if (errorText) screen.append(text("p", errorText, "err"));
    return;
  }
  if (flow.screen === "token") return renderToken();
  if (flow.screen === "webhooks") return renderHooks();
  if (flow.screen === "servers") return renderServers();
  screen.append(text("p", "Press any key to continue", "log"));
}

function renderToken() {
  screen.append(text("div", "Add token", "title"));
  const rows = [
    { id: "token", label: "token" },
    { id: "keep", label: "keep on this machine  " + (fields.keep ? "yes" : "no") },
    { id: "save", label: "save token" },
    { id: "service", label: "keychain service" },
    { id: "account", label: "keychain account" },
    { id: "keychain", label: "read keychain" },
    { id: "back", label: "back" },
  ];
  if (tokenIndex >= rows.length) tokenIndex = 0;
  rows.forEach((item, index) => {
    const row = rowButton("", index === tokenIndex, false, () => {
      tokenIndex = index;
      tokenAction();
    });
    if (typing === item.id) {
      row.textContent = "";
      row.append(document.createTextNode(item.label + " "));
      const input = document.createElement("input");
      input.type = item.id === "token" ? "password" : "text";
      input.value = draft;
      input.autocomplete = "off";
      input.spellcheck = false;
      input.addEventListener("input", () => {
        draft = input.value;
      });
      input.addEventListener("click", (event) => event.stopPropagation());
      input.addEventListener("keydown", (event) => {
        event.stopPropagation();
        if (event.key === "Enter") {
          event.preventDefault();
          commitToken();
        } else if (event.key === "Escape") {
          event.preventDefault();
          escapeToken();
        }
      });
      row.append(input);
      screen.append(row);
      input.focus();
      return;
    }
    let extra = "";
    if (item.id === "token" && fields.token) extra = "  set";
    if (item.id === "service" && fields.service) extra = "  " + fields.service;
    if (item.id === "account" && fields.account) extra = "  " + fields.account;
    row.textContent = index + 1 + "  " + item.label + extra;
    screen.append(row);
  });
  if (errorText) screen.append(text("p", errorText, "err"));
}

function renderHooks() {
  screen.append(text("div", "Webhook settings", "title"));
  screen.append(text("p", "On start, this account creates a new server.", "log"));
  screen.append(text("p", "It adds one channel for each channel you selected,", "log"));
  screen.append(text("p", "and a webhook named the same as that channel.", "log"));
  const name = snap.options.dest_name || "mirror";
  const rows = [
    { id: "name", label: "server name  " + name },
    { id: "backfill", label: "backfill  " + backfillLabel(snap.options.backfill) },
    { id: "threads", label: "threads  " + (snap.options.include_threads ? "on" : "off") },
    { id: "fresh", label: "new server on next start" },
    { id: "back", label: "back" },
  ];
  if (hookIndex >= rows.length) hookIndex = 0;
  rows.forEach((item, index) => {
    const row = rowButton(index + 1 + "  " + item.label, index === hookIndex, false, () => {
      hookIndex = index;
      hookAction();
    });
    if (typing === "name" && item.id === "name") {
      row.textContent = "";
      row.append(document.createTextNode("server name "));
      const input = document.createElement("input");
      input.value = draft;
      input.autocomplete = "off";
      input.spellcheck = false;
      input.addEventListener("input", () => {
        draft = input.value;
      });
      input.addEventListener("click", (event) => event.stopPropagation());
      input.addEventListener("keydown", (event) => {
        event.stopPropagation();
        if (event.key === "Enter") {
          event.preventDefault();
          commitName();
        } else if (event.key === "Escape") {
          event.preventDefault();
          cancelEdit();
        }
      });
      row.append(input);
      screen.append(row);
      input.focus();
      return;
    }
    screen.append(row);
  });
  if (errorText) screen.append(text("p", errorText, "err"));
}

function renderServers() {
  if (depth === "channels" && activeGuild) {
    screen.append(text("div", activeGuild.name, "title"));
    if (!channelRows.length) {
      screen.append(text("p", "nothing in that server can be read", "log"));
      return;
    }
    channelRows.forEach((channel, index) => {
      const mark = picked.has(channel.id) ? "[x] " : "[ ] ";
      const parent = channel.parent ? channel.parent + " / " : "";
      screen.append(
        rowButton(mark + parent + "#" + channel.name, index === localIndex, false, () => {
          localIndex = index;
          toggleChannel(channel);
        }),
      );
    });
    if (errorText) screen.append(text("p", errorText, "err"));
    return;
  }
  screen.append(text("div", "Select servers", "title"));
  if (!guilds.length) {
    screen.append(text("p", "no servers", "log"));
    return;
  }
  guilds.forEach((guild, index) => {
    const mark = guildSelected(guild.id) ? "[x] " : "[ ] ";
    screen.append(
      rowButton(mark + guild.name, index === localIndex, false, () => {
        localIndex = index;
        toggleGuild(guild);
      }),
    );
  });
  if (errorText) screen.append(text("p", errorText, "err"));
}

function tokenRows() {
  return ["token", "keep", "save", "service", "account", "keychain", "back"];
}

function hookRows() {
  return ["name", "backfill", "threads", "fresh", "back"];
}

async function tokenAction() {
  const id = tokenRows()[tokenIndex];
  errorText = "";
  if ((typing === "token" || typing === "service" || typing === "account") && id !== typing) commitToken();
  if (id === "token" || id === "service" || id === "account") {
    if (typing === id) return;
    typing = id;
    draft = fields[id] || "";
    render();
    return;
  }
  if (id === "keep") {
    fields.keep = !fields.keep;
    render();
    return;
  }
  if (id === "back") {
    flow = reduce({ screen: "token", index: 0 }, { key: "Escape" }, ctx());
    render();
    return;
  }
  if (id === "save") return signIn({ token: fields.token, keep: fields.keep });
  if (id === "keychain") {
    return signIn({
      keychain_service: fields.service,
      keychain_account: fields.account,
      keep: fields.keep,
    });
  }
}

function commitToken() {
  if (typing === "token" || typing === "service" || typing === "account") fields[typing] = draft;
  typing = null;
  draft = "";
  render();
}

function commitName() {
  if (typing !== "name") return;
  snap.options.dest_name = draft.trim() || "mirror";
  typing = null;
  draft = "";
  return saveOptions();
}

function cancelEdit() {
  typing = null;
  draft = "";
  render();
}

function typeToken(text) {
  typing = "token";
  draft = text;
  render();
}

function escapeToken() {
  if (typing === "token") commitToken();
  else cancelEdit();
}

async function signIn(body) {
  busy = true;
  try {
    apply(await api("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
    fields.token = "";
    errorText = "";
    flow = reduce({ screen: "token", index: 0 }, { key: "Escape" }, ctx());
  } catch (error) {
    errorText = error.message;
  } finally {
    busy = false;
    render();
  }
}

async function hookAction() {
  const id = hookRows()[hookIndex];
  errorText = "";
  if (typing === "name" && id !== "name") await commitName();
  if (id === "name") {
    if (typing === "name") return;
    typing = "name";
    draft = snap.options.dest_name || "mirror";
    render();
    return;
  }
  if (id === "backfill") return stepBackfill(1, true);
  if (id === "threads") {
    snap.options.include_threads = !snap.options.include_threads;
    return saveOptions();
  }
  if (id === "fresh") {
    busy = true;
    try {
      apply(await api("/api/destination/reset", { method: "POST" }));
      errorText = "";
    } catch (error) {
      errorText = error.message;
    } finally {
      busy = false;
      render();
    }
    return;
  }
  if (id === "back") {
    flow = reduce({ screen: "webhooks", index: 0 }, { key: "Escape" }, ctx());
    render();
  }
}

function stepBackfill(direction, wrap) {
  const found = BACKFILL.indexOf(Number(snap.options.backfill) || 0);
  const current = found < 0 ? 0 : found;
  const next = wrap
    ? (current + direction + BACKFILL.length) % BACKFILL.length
    : Math.max(0, Math.min(BACKFILL.length - 1, current + direction));
  snap.options.backfill = BACKFILL[next];
  return saveOptions();
}

async function shiftHook(direction) {
  const id = hookRows()[hookIndex];
  if (id === "backfill") {
    await stepBackfill(direction, false);
  } else if (id === "threads") {
    snap.options.include_threads = direction > 0;
    await saveOptions();
  }
}

function saveOptions() {
  if (!loaded) {
    errorText = "not connected yet";
    render();
    return saveChain;
  }
  const seq = ++saveSeq;
  pending += 1;
  const run = async () => {
    try {
      const next = await api("/api/setup", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          backfill: snap.options.backfill,
          include_threads: snap.options.include_threads,
          mirror: snap.options.mirror,
          global_webhook: "",
          dest_name: snap.options.dest_name || "mirror",
          channels: [...picked.values()],
        }),
      });
      if (seq !== saveSeq) return;
      apply(next);
      errorText = "";
    } catch (error) {
      if (seq !== saveSeq) return;
      errorText = error.message;
    } finally {
      pending -= 1;
      saveDone += 1;
    }
    render();
  };
  saveChain = saveChain.then(run, run);
  return saveChain;
}

function saveSelection() {
  return saveOptions();
}

async function toggleGuild(guild) {
  busy = true;
  errorText = "";
  try {
    const data = await api("/api/guilds/" + guild.id + "/channels");
    const list = data.channels || [];
    if (!list.length) {
      errorText = "nothing in that server can be read";
      render();
      return;
    }
    const chosen = list.some((channel) => picked.has(channel.id));
    list.forEach((channel) => {
      if (chosen) picked.delete(channel.id);
      else remember(guild, channel);
    });
    await saveSelection();
  } catch (error) {
    errorText = error.message;
    render();
  } finally {
    busy = false;
  }
}

function remember(guild, channel) {
  const previous = picked.get(channel.id);
  picked.set(channel.id, {
    channel_id: channel.id,
    guild_id: guild.id,
    guild_name: guild.name,
    channel_name: channel.name,
    parent: channel.parent || "",
    topic: channel.topic || "",
    webhook_url: previous && previous.webhook_url ? previous.webhook_url : "",
    enabled: true,
  });
}

async function toggleChannel(channel) {
  if (!activeGuild) return;
  if (picked.has(channel.id)) picked.delete(channel.id);
  else remember(activeGuild, channel);
  await saveSelection();
}

async function openChannels(guild) {
  busy = true;
  errorText = "";
  try {
    const data = await api("/api/guilds/" + guild.id + "/channels");
    activeGuild = guild;
    channelRows = data.channels || [];
    depth = "channels";
    localIndex = 0;
  } catch (error) {
    errorText = error.message;
  } finally {
    busy = false;
    render();
  }
}

async function selectAllChannels() {
  if (!activeGuild) return;
  channelRows.forEach((channel) => remember(activeGuild, channel));
  await saveSelection();
}

async function press(key, plain) {
  if (busy) return;
  if (flow.screen === "welcome") {
    flow = reduce(flow, { key }, ctx());
    errorText = "";
    render();
    return;
  }
  if (flow.screen === "exit") {
    flow = { screen: "menu", index: 0, action: null, locked: false };
    errorText = "";
    render();
    return;
  }
  if (flow.screen === "running") {
    if (key === "Escape") {
      flow = { screen: "menu", index: 0, action: null, locked: false };
      render();
    } else if (key === "ArrowDown") screen.scrollBy(0, 48);
    else if (key === "ArrowUp") screen.scrollBy(0, -48);
    return;
  }
  if (flow.screen === "menu" || flow.screen === "settings") {
    const next = reduce(flow, { key }, ctx());
    if (next.locked) {
      flow = next;
      errorText = "add a working token first";
      render();
      return;
    }
    errorText = "";
    flow = next;
    if (next.action === "start") return begin();
    if (next.action === "exit") return halt();
    if (next.action === "servers") return loadGuilds();
    render();
    return;
  }
  if (flow.screen === "token") return tokenKey(key, plain);
  if (flow.screen === "webhooks") return hookKey(key);
  if (flow.screen === "servers") return serverKey(key);
}

function tokenKey(key, plain) {
  if (typing === "token" || typing === "service" || typing === "account") {
    if (key === "Enter") commitToken();
    else if (key === "Escape") escapeToken();
    return;
  }
  const rows = tokenRows();
  if (key === "ArrowDown") tokenIndex = (tokenIndex + 1) % rows.length;
  else if (key === "ArrowUp") tokenIndex = (tokenIndex - 1 + rows.length) % rows.length;
  else if (key === "Enter") return tokenAction();
  else if (key === "Escape") {
    flow = reduce(flow, { key: "Escape" }, ctx());
    typing = null;
  } else if (key >= "1" && key <= String(rows.length)) {
    tokenIndex = Number(key) - 1;
    return tokenAction();
  } else if (plain && rows[tokenIndex] === "token") return typeToken(key);
  render();
}

async function hookKey(key) {
  if (typing === "name") {
    if (key === "Enter") return commitName();
    if (key === "Escape") cancelEdit();
    return;
  }
  const rows = hookRows();
  if (key === "ArrowDown") hookIndex = (hookIndex + 1) % rows.length;
  else if (key === "ArrowUp") hookIndex = (hookIndex - 1 + rows.length) % rows.length;
  else if (key === "ArrowRight") return shiftHook(1);
  else if (key === "ArrowLeft") return shiftHook(-1);
  else if (key === "Enter") return hookAction();
  else if (key === "Escape") flow = reduce(flow, { key: "Escape" }, ctx());
  else if (key >= "1" && key <= String(rows.length)) {
    hookIndex = Number(key) - 1;
    return hookAction();
  }
  render();
}

async function serverKey(key) {
  if (depth === "channels") {
    if (key === "Escape") {
      depth = "guilds";
      localIndex = Math.max(0, guilds.findIndex((guild) => activeGuild && guild.id === activeGuild.id));
      render();
      return;
    }
    if (!channelRows.length) return;
    if (key === "ArrowDown") localIndex = Math.min(channelRows.length - 1, localIndex + 1);
    else if (key === "ArrowUp") localIndex = Math.max(0, localIndex - 1);
    else if (key === "Enter") return toggleChannel(channelRows[localIndex]);
    else if (key === "a" || key === "A") return selectAllChannels();
    render();
    return;
  }
  if (key === "Escape") {
    flow = reduce(flow, { key: "Escape" }, ctx());
    render();
    return;
  }
  if (!guilds.length) return;
  if (key === "ArrowDown") localIndex = (localIndex + 1) % guilds.length;
  else if (key === "ArrowUp") localIndex = (localIndex - 1 + guilds.length) % guilds.length;
  else if (key === "Enter") return toggleGuild(guilds[localIndex]);
  else if (key === "ArrowRight") return openChannels(guilds[localIndex]);
  render();
}

async function loadGuilds() {
  busy = true;
  errorText = "";
  depth = "guilds";
  localIndex = 0;
  render();
  try {
    const body = await api("/api/guilds");
    guilds = body.guilds || [];
  } catch (error) {
    errorText = error.message;
    flow = { screen: "settings", index: 1, action: null, locked: false };
  } finally {
    busy = false;
    render();
  }
}

async function begin() {
  busy = true;
  errorText = "";
  flow = { screen: "running", index: 0, action: null, locked: false };
  render();
  try {
    apply(await api("/api/start", { method: "POST" }));
  } catch (error) {
    errorText = error.message;
    flow = { screen: "menu", index: 0, action: null, locked: false };
  } finally {
    busy = false;
    render();
  }
}

async function halt() {
  busy = true;
  try {
    apply(await api("/api/stop", { method: "POST" }));
  } catch (error) {
    errorText = error.message;
  } finally {
    busy = false;
    flow = { screen: "exit", index: 0, action: null, locked: false };
    render();
  }
}

function rememberMessage(message) {
  place(message);
  if (late) late.push(message);
}

function place(message) {
  const index = messages.findIndex((item) => item.id === message.id);
  if (index >= 0) messages[index] = message;
  else messages.push(message);
  if (messages.length > 200) messages.splice(0, messages.length - 200);
}

function connect() {
  const source = new EventSource("/api/events");
  source.onopen = async () => {
    opens += 1;
    if (opens === 1 && loaded) return;
    const seq = saveSeq;
    const done = saveDone;
    lateUsers += 1;
    if (!late) late = [];
    try {
      const [snapshot, feed] = await Promise.all([api("/api/state"), api("/api/feed")]);
      if (!pending && seq === saveSeq && done === saveDone) {
        if (!loaded) errorText = "";
        apply(snapshot);
        loaded = true;
      }
      const held = late.slice();
      messages = feed.messages || [];
      held.forEach(place);
      if (!typing) render();
    } catch {
    } finally {
      lateUsers -= 1;
      if (!lateUsers) late = null;
    }
  };
  source.onerror = () => {
    if (source.readyState !== source.CLOSED) return;
    source.close();
    setTimeout(connect, 2000);
  };
  source.onmessage = (event) => {
    let item;
    try {
      item = JSON.parse(event.data);
    } catch {
      return;
    }
    if (item.kind === "log" && item.text) {
      snap.log = [item.text].concat(snap.log || []).slice(0, 60);
      if (flow.screen === "running") render();
    } else if (item.kind === "status") {
      snap.running = !!item.running;
      snap.status = item.status || snap.status;
      statusNode.textContent = snap.status;
    } else if (item.kind === "message" && item.message) {
      rememberMessage(item.message);
      if (flow.screen === "running") render();
    }
  };
}

document.addEventListener("keydown", (event) => {
  if (event.target && event.target.tagName === "INPUT") return;
  const plain = event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
  if (plain || ["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Enter", "Escape"].includes(event.key)) {
    event.preventDefault();
  }
  press(event.key, plain);
});

document.addEventListener("paste", (event) => {
  if (busy || flow.screen !== "token" || typing || tokenRows()[tokenIndex] !== "token") return;
  event.preventDefault();
  typeToken(event.clipboardData.getData("text"));
});

term.addEventListener("click", () => {
  if (!typing) term.focus();
});

try {
  const [snapshot, feed] = await Promise.all([api("/api/state"), api("/api/feed")]);
  apply(snapshot);
  messages = feed.messages || [];
  loaded = true;
} catch (error) {
  errorText = error.message;
}
connect();
render();
term.focus();
