"use strict";

const BACKFILL = [0, 25, 50, 100, 250, 500];

const state = {
  user: null,
  running: false,
  status: "idle",
  options: { backfill: 0, include_threads: false, global_webhook: "", mirror: false },
  selection: [],
  log: [],
  messages: [],
};

const ui = {
  screen: "home",
  index: 0,
  guilds: [],
  guild: null,
  typing: null,
  busy: false,
  error: "",
  fields: { token: "", service: "", account: "", keep: true },
};

const term = document.getElementById("term");
const screen = document.getElementById("screen");
const whoNode = document.getElementById("who");
const statusNode = document.getElementById("status");
const hintNode = document.getElementById("hint");

function text(tag, value, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value || "";
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

function backfillLabel(value) {
  const amount = Number(value) || 0;
  return amount ? "last " + amount : "live only";
}

function apply(next) {
  state.user = next.user;
  state.running = !!next.running;
  state.status = next.status || "idle";
  state.options = next.options || state.options;
  state.selection = next.selection || [];
  state.log = next.log || state.log;
  whoNode.textContent = next.user
    ? next.user.global_name || next.user.username || next.user.id
    : "signed out";
  statusNode.textContent = state.running ? state.status : state.status || "idle";
}

function items() {
  if (ui.screen === "home") {
    return [
      { id: "account", label: "account" },
      { id: "servers", label: "servers" },
      { id: "transcript", label: "transcript" },
      { id: "backfill", label: "backfill", value: backfillLabel(state.options.backfill), adjust: true },
      { id: "threads", label: "threads", value: state.options.include_threads ? "on" : "off", adjust: true },
      { id: "start", label: "start" },
      { id: "stop", label: "stop" },
    ];
  }
  if (ui.screen === "account") {
    return [
      { id: "token", label: "token", field: "token", secret: true },
      { id: "service", label: "keychain service", field: "service" },
      { id: "account-name", label: "keychain account", field: "account" },
      { id: "keep", label: "keep on this machine", value: ui.fields.keep ? "yes" : "no", adjust: true },
      { id: "use-token", label: "use token" },
      { id: "use-keychain", label: "read keychain" },
      { id: "forget", label: "forget" },
    ];
  }
  if (ui.screen === "servers") {
    if (!ui.guilds.length) return [{ id: "empty", label: "no servers" }];
    return ui.guilds.map((guild) => ({ id: guild.id, label: guild.name, guild }));
  }
  if (ui.screen === "confirm" && ui.guild) {
    return [
      { id: "copy", label: "copy " + ui.guild.name },
      { id: "back", label: "back" },
    ];
  }
  if (ui.screen === "job") return [{ id: "back", label: "back" }];
  return [];
}

function hint() {
  if (ui.typing) return "enter keeps it, esc cancels the edit";
  if (ui.screen === "transcript") return "up and down scroll, esc back";
  if (ui.screen === "confirm") return "enter copies the whole server";
  if (ui.screen === "servers") return "up and down move, enter selects the server";
  if (ui.screen === "job") return "esc back";
  return "up and down move, enter opens, left and right change a value";
}

function render() {
  const rows = items();
  if (ui.index >= rows.length) ui.index = Math.max(0, rows.length - 1);
  if (ui.index < 0) ui.index = 0;
  hintNode.textContent = hint();
  screen.replaceChildren();
  if (ui.screen === "transcript") {
    renderTranscript();
    return;
  }
  const title = text("div", titleFor(), "title");
  screen.append(title);
  if (ui.screen === "confirm" && ui.guild) {
    screen.append(text("p", "Creates a new server with the same name.", "log"));
    screen.append(text("p", "Only channels you can open and read are copied.", "log"));
    screen.append(text("p", "Each one gets a webhook named the same as the channel.", "log"));
    screen.append(text("p", "This replaces the current follow list.", "log"));
  }
  if (ui.screen === "job") {
    state.log.slice().reverse().forEach((line) => screen.append(text("p", line, "log")));
  }
  rows.forEach((item, index) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = index === ui.index ? "row on" : "row";
    row.dataset.index = String(index);
    if (index === ui.index) row.dataset.selected = "true";
    if (item.field && ui.typing === item.field) {
      row.append(document.createTextNode(item.label));
      const input = document.createElement("input");
      input.type = item.secret ? "password" : "text";
      input.value = ui.fields[item.field] || "";
      input.autocomplete = "off";
      input.spellcheck = false;
      input.addEventListener("input", () => {
        ui.fields[item.field] = input.value;
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          ui.typing = null;
          render();
        } else if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          ui.typing = null;
          render();
        } else {
          event.stopPropagation();
        }
      });
      row.append(input);
      screen.append(row);
      input.focus();
      return;
    }
    row.append(document.createTextNode(item.label));
    if (item.field) {
      const shown = ui.fields[item.field] ? (item.secret ? " set" : " " + ui.fields[item.field]) : " ";
      row.append(text("span", shown, "val"));
    } else if (item.value) {
      row.append(text("span", "  " + item.value, "val"));
    }
    row.addEventListener("click", () => {
      ui.index = index;
      activate();
    });
    screen.append(row);
  });
  if (ui.error) screen.append(text("p", ui.error, "err"));
  const selected = screen.querySelector(".row.on");
  if (selected && ui.screen !== "job") selected.scrollIntoView({ block: "nearest" });
  if (ui.screen === "job") screen.scrollTop = screen.scrollHeight;
}

function titleFor() {
  if (ui.screen === "account") return "account";
  if (ui.screen === "servers") return "servers";
  if (ui.screen === "confirm") return ui.guild ? ui.guild.name : "copy";
  if (ui.screen === "job") return "copy";
  return "menu";
}

function renderTranscript() {
  screen.append(text("div", "transcript", "title"));
  if (!state.messages.length) {
    screen.append(text("p", "nothing here yet", "empty"));
    return;
  }
  state.messages.forEach((message) => {
    const block = document.createElement("article");
    block.className = message.deleted ? "msg deleted" : "msg";
    const meta = document.createElement("span");
    meta.className = "meta";
    const when = message.timestamp ? message.timestamp.slice(11, 16) : "";
    meta.textContent = [when, message.guild_name, message.channel_name ? "#" + message.channel_name : "", message.author]
      .filter(Boolean)
      .join("  ");
    block.append(meta);
    if (message.content) block.append(text("span", message.content, "body"));
    if (message.deleted) block.append(text("span", "deleted", "body"));
    screen.append(block);
  });
}

function openScreen(name) {
  ui.screen = name;
  ui.index = 0;
  ui.typing = null;
  ui.error = "";
  render();
}

async function activate() {
  if (ui.busy || ui.typing) return;
  const item = items()[ui.index];
  if (!item) return;
  ui.error = "";
  if (item.field) {
    ui.typing = item.field;
    render();
    return;
  }
  if (item.id === "account") return openScreen("account");
  if (item.id === "servers") return loadServers();
  if (item.id === "transcript") return openScreen("transcript");
  if (item.id === "back" || item.id === "empty") return openScreen(ui.screen === "confirm" ? "servers" : "home");
  if (item.id === "keep") {
    ui.fields.keep = !ui.fields.keep;
    render();
    return;
  }
  if (item.id === "use-token") return signIn(ui.fields.token);
  if (item.id === "use-keychain") return signInKeychain();
  if (item.id === "forget") return forget();
  if (item.id === "start") return run("/api/start");
  if (item.id === "stop") return run("/api/stop");
  if (item.guild) {
    ui.guild = item.guild;
    return openScreen("confirm");
  }
  if (item.id === "copy") return copyServer();
}

async function adjust(direction) {
  const item = items()[ui.index];
  if (!item || !item.adjust || ui.busy) return;
  if (item.id === "keep") {
    ui.fields.keep = direction > 0;
    render();
    return;
  }
  if (item.id === "threads") {
    state.options.include_threads = direction > 0;
    await saveOptions();
    return;
  }
  if (item.id === "backfill") {
    const current = BACKFILL.indexOf(Number(state.options.backfill) || 0);
    const next = Math.max(0, Math.min(BACKFILL.length - 1, (current < 0 ? 0 : current) + direction));
    state.options.backfill = BACKFILL[next];
    await saveOptions();
  }
}

async function saveOptions() {
  try {
    const body = await api("/api/setup", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        backfill: state.options.backfill,
        include_threads: state.options.include_threads,
        mirror: state.options.mirror,
        global_webhook: state.options.global_webhook || "",
        channels: state.selection,
      }),
    });
    apply(body);
    ui.error = "";
  } catch (error) {
    ui.error = error.message;
  }
  render();
}

async function loadServers() {
  ui.busy = true;
  ui.error = "";
  render();
  try {
    const body = await api("/api/guilds");
    ui.guilds = body.guilds || [];
    openScreen("servers");
  } catch (error) {
    ui.error = error.message;
    render();
  } finally {
    ui.busy = false;
  }
}

async function signIn(token) {
  ui.busy = true;
  try {
    const body = await api("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, keep: ui.fields.keep }),
    });
    apply(body);
    ui.fields.token = "";
    openScreen("home");
  } catch (error) {
    ui.error = error.message;
    render();
  } finally {
    ui.busy = false;
  }
}

async function signInKeychain() {
  ui.busy = true;
  try {
    const body = await api("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        keychain_service: ui.fields.service,
        keychain_account: ui.fields.account,
        keep: ui.fields.keep,
      }),
    });
    apply(body);
    openScreen("home");
  } catch (error) {
    ui.error = error.message;
    render();
  } finally {
    ui.busy = false;
  }
}

async function forget() {
  try {
    apply(await api("/api/session", { method: "DELETE" }));
    openScreen("home");
  } catch (error) {
    ui.error = error.message;
    render();
  }
}

async function run(path) {
  ui.busy = true;
  try {
    apply(await api(path, { method: "POST" }));
    ui.error = "";
  } catch (error) {
    ui.error = error.message;
  } finally {
    ui.busy = false;
    render();
  }
}

async function copyServer() {
  if (!ui.guild || ui.busy) return;
  ui.busy = true;
  ui.screen = "job";
  ui.index = 0;
  ui.error = "";
  render();
  try {
    const body = await api("/api/guilds/" + ui.guild.id + "/copy", { method: "POST" });
    apply(body);
  } catch (error) {
    ui.error = error.message;
  } finally {
    ui.busy = false;
    render();
  }
}

function onKey(event) {
  if (event.target && event.target.tagName === "INPUT") return;
  if (ui.screen === "transcript") {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      screen.scrollBy(0, 48);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      screen.scrollBy(0, -48);
    } else if (event.key === "Escape") {
      event.preventDefault();
      openScreen("home");
    }
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    ui.index = Math.min(items().length - 1, ui.index + 1);
    render();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    ui.index = Math.max(0, ui.index - 1);
    render();
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    adjust(1);
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    adjust(-1);
  } else if (event.key === "Enter") {
    event.preventDefault();
    activate();
  } else if (event.key === "Escape") {
    event.preventDefault();
    if (ui.screen !== "home") openScreen(ui.screen === "confirm" ? "servers" : "home");
  }
}

function rememberMessage(message) {
  const index = state.messages.findIndex((item) => item.id === message.id);
  if (index >= 0) state.messages[index] = message;
  else state.messages.push(message);
  if (state.messages.length > 500) state.messages.splice(0, state.messages.length - 500);
}

function connect() {
  const source = new EventSource("/api/events");
  source.onmessage = (event) => {
    let item;
    try {
      item = JSON.parse(event.data);
    } catch {
      return;
    }
    if (item.kind === "log" && item.text) {
      state.log.unshift(item.text);
      if (ui.screen === "job") render();
    } else if (item.kind === "status") {
      state.running = !!item.running;
      state.status = item.status || state.status;
      statusNode.textContent = state.status;
    } else if (item.kind === "message" && item.message) {
      rememberMessage(item.message);
      if (ui.screen === "transcript") {
        const atEnd = screen.scrollHeight - screen.scrollTop - screen.clientHeight < 80;
        render();
        if (atEnd) screen.scrollTop = screen.scrollHeight;
      }
    }
  };
}

document.addEventListener("keydown", onKey);
term.addEventListener("click", () => {
  if (!ui.typing) term.focus();
});

boot();

async function boot() {
  try {
    const [snapshot, feed] = await Promise.all([api("/api/state"), api("/api/feed")]);
    apply(snapshot);
    state.messages = feed.messages || [];
  } catch (error) {
    ui.error = error.message;
  }
  connect();
  render();
  term.focus();
}
