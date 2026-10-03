"use strict";

const state = {
  user: null,
  running: false,
  status: "idle",
  options: { backfill: 100, include_threads: false, global_webhook: "", mirror: false },
  selection: [],
  guilds: [],
  activeGuild: null,
  channels: [],
  picked: new Map(),
};

const feedNode = document.getElementById("feed");
const logNode = document.getElementById("log");
const statusNode = document.getElementById("status");
const whoNode = document.getElementById("who");

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
  if (!response.ok) {
    throw new Error(body.error || "request failed");
  }
  return body;
}

function showError(error) {
  const line = document.createElement("li");
  line.className = "error";
  line.textContent = error.message || String(error);
  logNode.prepend(line);
}

function applyState(next) {
  state.user = next.user;
  state.running = next.running;
  state.status = next.status;
  state.options = next.options;
  state.selection = next.selection || [];
  document.getElementById("backfill").value = String(next.options.backfill || 0);
  document.getElementById("threads").checked = !!next.options.include_threads;
  document.getElementById("webhook").value = next.options.global_webhook || "";
  document.getElementById("mirror").checked = !!next.options.mirror;
  state.picked = new Map();
  state.selection.forEach((row) => {
    if (row.enabled) {
      state.picked.set(row.channel_id, row);
    }
  });
  whoNode.textContent = next.user
    ? next.user.global_name || next.user.username || next.user.id
    : "No account loaded";
  statusNode.textContent = next.running ? next.status || "live" : next.status || "idle";
  statusNode.className = "pill" + (next.running && next.status === "live" ? " live" : "");
  logNode.replaceChildren();
  (next.log || []).slice().reverse().forEach((line) => {
    logNode.append(text("li", line));
  });
  drawChannels();
}

function setupBody() {
  const guild = state.guilds.find((item) => item.id === state.activeGuild);
  if (guild) {
    state.channels.forEach((channel) => {
      const box = document.querySelector('input[data-channel="' + channel.id + '"]');
      if (!box) return;
      const hook = document.querySelector('input[data-hook="' + channel.id + '"]');
      if (box.checked) {
        state.picked.set(channel.id, {
          channel_id: channel.id,
          guild_id: guild.id,
          guild_name: guild.name,
          channel_name: channel.name,
          webhook_url: hook ? hook.value.trim() : "",
          enabled: true,
        });
      } else {
        state.picked.delete(channel.id);
      }
    });
  }
  const fresh = [];
  state.picked.forEach((row) => fresh.push(row));
  return {
    backfill: Number(document.getElementById("backfill").value || 0),
    include_threads: document.getElementById("threads").checked,
    global_webhook: document.getElementById("webhook").value.trim(),
    mirror: document.getElementById("mirror").checked,
    channels: fresh,
  };
}

async function save() {
  const body = setupBody();
  const next = await api("/api/setup", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  applyState(next);
}

async function loadGuilds() {
  const data = await api("/api/guilds");
  state.guilds = data.guilds || [];
  const host = document.getElementById("guilds");
  host.replaceChildren();
  if (!state.guilds.length) {
    host.append(text("p", "No servers on this account.", "hint"));
    return;
  }
  state.guilds.forEach((guild) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "guild quiet" + (guild.id === state.activeGuild ? " active" : "");
    button.textContent = guild.name;
    button.addEventListener("click", () => {
      state.activeGuild = guild.id;
      loadGuilds().catch(showError);
      loadChannels(guild.id).catch(showError);
    });
    host.append(button);
  });
}

async function loadChannels(guildId) {
  const data = await api("/api/guilds/" + guildId + "/channels");
  state.channels = data.channels || [];
  drawChannels();
}

function drawChannels() {
  const host = document.getElementById("channels");
  host.replaceChildren();
  if (!state.activeGuild) {
    host.append(text("p", "Pick a server.", "hint"));
    return;
  }
  const bar = document.createElement("div");
  bar.className = "actions";
  const all = document.createElement("button");
  all.type = "button";
  all.className = "quiet";
  all.textContent = "All text";
  all.addEventListener("click", () => {
    host.querySelectorAll('input[data-channel]').forEach((box) => {
      box.checked = true;
      box.dispatchEvent(new Event("change"));
    });
  });
  const none = document.createElement("button");
  none.type = "button";
  none.className = "quiet";
  none.textContent = "Clear server";
  none.addEventListener("click", () => {
    const guild = state.guilds.find((item) => item.id === state.activeGuild);
    host.querySelectorAll('input[data-channel]').forEach((box) => {
      box.checked = false;
    });
    if (guild) {
      Array.from(state.picked.keys()).forEach((id) => {
        const row = state.picked.get(id);
        if (row && row.guild_id === guild.id) state.picked.delete(id);
      });
    }
    drawChannels();
  });
  bar.append(all, none);
  host.append(bar);
  let parent = null;
  state.channels.forEach((channel) => {
    if (channel.parent !== parent) {
      parent = channel.parent;
      if (parent) host.append(text("h2", parent));
    }
    const row = document.createElement("div");
    row.className = "channel";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.dataset.channel = channel.id;
    const known = state.picked.get(channel.id);
    box.checked = !!known;
    const name = text("span", "#" + channel.name);
    const hook = document.createElement("input");
    hook.dataset.hook = channel.id;
    hook.placeholder = "webhook override";
    hook.autocomplete = "off";
    hook.spellcheck = false;
    hook.value = known && known.webhook_url ? known.webhook_url : "";
    const remember = () => {
      const guild = state.guilds.find((item) => item.id === state.activeGuild);
      if (!guild) return;
      if (box.checked) {
        state.picked.set(channel.id, {
          channel_id: channel.id,
          guild_id: guild.id,
          guild_name: guild.name,
          channel_name: channel.name,
          webhook_url: hook.value.trim(),
          enabled: true,
        });
      } else {
        state.picked.delete(channel.id);
      }
    };
    box.addEventListener("change", remember);
    hook.addEventListener("change", remember);
    const top = document.createElement("div");
    top.className = "channel-top";
    top.append(box, name);
    const wrap = document.createElement("label");
    wrap.className = "hook";
    wrap.append(hook);
    row.append(top, wrap);
    host.append(row);
  });
}

function allowedUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && (parsed.hostname === "cdn.discordapp.com" || parsed.hostname === "media.discordapp.net");
  } catch {
    return false;
  }
}

function renderMessage(message) {
  const filter = document.getElementById("filter").value.trim().toLowerCase();
  const blob = [message.guild_name, message.channel_name, message.author, message.content].join(" ").toLowerCase();
  let node = document.getElementById("m-" + message.id);
  if (filter && blob.indexOf(filter) === -1) {
    if (node) node.remove();
    return;
  }
  if (!node) {
    const hint = feedNode.querySelector(".hint");
    if (hint) hint.remove();
    node = document.createElement("article");
    node.id = "m-" + message.id;
    feedNode.append(node);
  }
  node.className = message.deleted ? "deleted" : "";
  node.replaceChildren();
  const face = document.createElement("span");
  face.className = "face";
  if (message.avatar && allowedUrl(message.avatar)) {
    const img = document.createElement("img");
    img.src = message.avatar;
    img.alt = "";
    face.append(img);
  } else {
    face.textContent = (message.author || "?").slice(0, 1).toUpperCase();
  }
  const main = document.createElement("div");
  const meta = document.createElement("div");
  meta.className = "meta";
  const when = message.timestamp ? message.timestamp.slice(11, 16) : "";
  meta.append(
    text("strong", message.author || "member"),
    text("span", (message.guild_name ? message.guild_name + " / " : "") + "#" + (message.channel_name || "")),
  );
  if (message.edited) meta.append(text("span", "edited", "tag"));
  if (message.deleted) meta.append(text("span", "deleted", "tag"));
  if (when) meta.append(text("span", when, "when"));
  main.append(meta);
  if (message.reply) main.append(text("div", message.reply, "reply"));
  if (message.content) main.append(text("div", message.content, "body"));
  if (message.embeds && message.embeds.length) {
    message.embeds.forEach((embed) => {
      const block = document.createElement("div");
      block.className = "reply";
      if (embed.title) block.append(text("div", embed.title));
      if (embed.description) block.append(text("div", embed.description, "body"));
      main.append(block);
    });
  }
  if (message.stickers && message.stickers.length) {
    main.append(text("div", "stickers: " + message.stickers.join(", "), "reply"));
  }
  if (message.attachments && message.attachments.length) {
    const box = document.createElement("div");
    box.className = "attach";
    message.attachments.forEach((file) => {
      if (allowedUrl(file.url) && String(file.content_type || "").indexOf("image/") === 0) {
        const img = document.createElement("img");
        img.src = file.url;
        img.alt = file.name || "image";
        box.append(img);
      }
      if (String(file.url || "").indexOf("https://") === 0) {
        const link = document.createElement("a");
        link.href = file.url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = file.name || "file";
        box.append(link);
      }
    });
    main.append(box);
  }
  const reactions = Object.keys(message.reactions || {});
  if (reactions.length) {
    main.append(
      text(
        "div",
        reactions.map((name) => name + " " + message.reactions[name]).join("  "),
        "reply",
      ),
    );
  }
  node.append(face, main);
}

function stickToEnd() {
  const near = feedNode.scrollHeight - feedNode.scrollTop - feedNode.clientHeight < 120;
  if (near) feedNode.scrollTop = feedNode.scrollHeight;
}

async function loadFeed() {
  const data = await api("/api/feed");
  feedNode.replaceChildren();
  const messages = data.messages || [];
  if (!messages.length) {
    feedNode.append(text("p", "Nothing here yet. Pick channels and start.", "hint"));
    return;
  }
  messages.forEach(renderMessage);
  feedNode.scrollTop = feedNode.scrollHeight;
}

function listen() {
  const source = new EventSource("/api/events");
  source.onmessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (payload.kind === "message") {
      renderMessage(payload.message);
      stickToEnd();
    } else if (payload.kind === "log") {
      logNode.prepend(text("li", payload.text));
    } else if (payload.kind === "status") {
      state.running = payload.running;
      state.status = payload.status;
      statusNode.textContent = payload.running ? payload.status : "stopped";
      statusNode.className = "pill" + (payload.status === "live" ? " live" : "");
    }
  };
}

document.getElementById("use-token").addEventListener("click", () => {
  api("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      token: document.getElementById("token").value,
      keep: document.getElementById("keep").checked,
    }),
  })
    .then((next) => {
      document.getElementById("token").value = "";
      applyState(next);
      return loadGuilds();
    })
    .catch(showError);
});

document.getElementById("use-keychain").addEventListener("click", () => {
  api("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      keychain_service: document.getElementById("kc-service").value,
      keychain_account: document.getElementById("kc-account").value,
      keep: document.getElementById("keep").checked,
    }),
  })
    .then((next) => {
      applyState(next);
      return loadGuilds();
    })
    .catch(showError);
});

document.getElementById("forget").addEventListener("click", () => {
  api("/api/session", { method: "DELETE" })
    .then((next) => {
      state.guilds = [];
      state.channels = [];
      state.activeGuild = null;
      applyState(next);
      document.getElementById("guilds").replaceChildren();
      document.getElementById("channels").replaceChildren();
    })
    .catch(showError);
});

document.getElementById("save").addEventListener("click", () => {
  save().catch(showError);
});

document.getElementById("start").addEventListener("click", () => {
  save()
    .then(() => api("/api/start", { method: "POST" }))
    .then(applyState)
    .catch(showError);
});

document.getElementById("stop").addEventListener("click", () => {
  api("/api/stop", { method: "POST" }).then(applyState).catch(showError);
});

document.getElementById("filter").addEventListener("input", () => {
  loadFeed().catch(showError);
});

api("/api/state")
  .then((next) => {
    applyState(next);
    listen();
    return loadFeed();
  })
  .then(() => {
    if (state.user) return loadGuilds();
    return null;
  })
  .catch(showError);
