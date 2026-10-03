import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { after, before } from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

export const DEFAULTS = { backfill: 0, include_threads: false, mirror: false, global_webhook: "", dest_name: "mirror", channels: [] };

const root = process.env.E2E_ROOT || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function pause(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function exited(child) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve) => child.once("exit", resolve));
}

async function kill(child) {
  if (child.exitCode === null && child.signalCode === null) {
    const done = exited(child);
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      child.kill("SIGTERM");
      const late = setTimeout(() => child.kill("SIGKILL"), 5000);
      await done;
      clearTimeout(late);
    }
    await done;
  }
}

async function launch(dir) {
  const port = await freePort();
  const base = "http://127.0.0.1:" + port;
  const child = spawn(python, ["-m", "mirror"], {
    cwd: root,
    env: {
      ...process.env,
      HOST: "127.0.0.1",
      PORT: String(port),
      DATA_DIR: dir,
      LOG_LEVEL: process.env.E2E_LOG || "CRITICAL",
      PYTHONUNBUFFERED: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  let out = "";
  child.stdout.on("data", (chunk) => (out += chunk));
  child.stderr.on("data", (chunk) => (out += chunk));
  child.on("error", (error) => (out += String(error)));
  const end = Date.now() + 15000;
  while (Date.now() < end) {
    if (child.exitCode !== null || child.signalCode !== null) return { child, port, base, out, dead: true };
    try {
      const response = await fetch(base + "/api/state");
      if (response.ok) return { child, port, base, out: "", dead: false };
    } catch {}
    await pause(100);
  }
  await kill(child);
  throw new Error("server did not answer on " + base + "\n" + out);
}

export async function boot() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mando e2e "));
  let run = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    run = await launch(dir);
    if (!run.dead) break;
    if (!/address already in use|10048|10013|errno 98/i.test(run.out) || attempt === 2) {
      fs.rmSync(dir, { recursive: true, force: true });
      throw new Error("server exited during boot\n" + run.out);
    }
  }
  const { child, port, base } = run;
  async function api(method, where, body) {
    const init = { method, headers: {} };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const response = await fetch(base + where, init);
    return response.json();
  }
  async function stop() {
    try {
      await fetch(base + "/api/stop", { method: "POST" });
    } catch {}
    await kill(child);
    try {
      fs.rmSync(dir, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
    } catch (error) {
      console.error("could not remove " + dir + ": " + error.message);
    }
  }
  return { base, port, dir, api, stop };
}

export function reset(api, extra) {
  return api("PUT", "/api/setup", { ...DEFAULTS, ...extra });
}

export function harness() {
  const h = { srv: null, browser: null };
  before(async () => {
    h.srv = await boot();
    try {
      h.browser = await chromium.launch();
    } catch (error) {
      await h.srv.stop();
      throw error;
    }
  });
  after(async () => {
    if (h.browser) await h.browser.close();
    if (h.srv) await h.srv.stop();
  });
  h.page = async (t) => {
    const context = await h.browser.newContext();
    t.after(() => context.close());
    const page = await context.newPage();
    page.setDefaultTimeout(6000);
    return page;
  };
  return h;
}

async function dump(page) {
  try {
    return await page.evaluate(() => ({
      title: (document.querySelector("#screen .title") || {}).textContent,
      rows: [...document.querySelectorAll("#screen .row")].map((e) => e.textContent),
      picked: (document.querySelector('#screen .row[data-selected="true"]') || {}).textContent,
      err: (document.querySelector("#screen .err") || {}).textContent,
      hint: document.getElementById("hint").textContent,
      who: document.getElementById("who").textContent,
      status: document.getElementById("status").textContent,
    }));
  } catch (error) {
    return error.message;
  }
}

export async function until(page, fn, arg, ms = 5000) {
  try {
    return await page.waitForFunction(fn, arg, { timeout: ms, polling: 50 });
  } catch (error) {
    throw new Error(error.message.split("\n")[0] + "\nwaited for " + String(fn) + " " + JSON.stringify(arg) + "\npage " + JSON.stringify(await dump(page)));
  }
}

export function rows(page) {
  return page.$$eval("#screen .row", (els) => els.map((e) => e.textContent));
}

export function picked(page) {
  return page.$eval('#screen .row[data-selected="true"]', (e) => e.textContent);
}

export function title(page, name) {
  return until(page, (want) => {
    const node = document.querySelector("#screen .title");
    return !!node && node.textContent === want;
  }, name);
}

export function row(page, index, label) {
  return until(page, ([i, want]) => {
    const node = document.querySelectorAll("#screen .row")[i];
    return !!node && node.textContent === want;
  }, [index, label]);
}

export function list(page, labels) {
  return until(page, (want) => JSON.stringify([...document.querySelectorAll("#screen .row")].map((e) => e.textContent)) === JSON.stringify(want), labels);
}

export function chosen(page, label) {
  return until(page, (want) => {
    const node = document.querySelector('#screen .row[data-selected="true"]');
    return !!node && node.textContent === want;
  }, label);
}

export function err(page, message) {
  return until(page, (want) => {
    const node = document.querySelector("#screen .err");
    return !!node && node.textContent === want;
  }, message);
}

export function said(page, id, value) {
  return until(page, ([where, want]) => document.getElementById(where).textContent === want, [id, value]);
}

export function click(page, index) {
  return page.locator("#screen .row").nth(index).click({ position: { x: 6, y: 6 } });
}

export async function open(page, base) {
  await page.goto(base + "/");
  await page.waitForSelector(".welcome");
}

export async function menu(page, base) {
  await open(page, base);
  await page.keyboard.press("Enter");
  await title(page, "menu");
}

export async function settings(page, base) {
  await menu(page, base);
  await page.keyboard.press("2");
  await title(page, "settings");
}

export async function hooks(page, base) {
  await settings(page, base);
  await page.keyboard.press("3");
  await title(page, "Webhook settings");
}

export async function token(page, base) {
  await settings(page, base);
  await page.keyboard.press("1");
  await title(page, "Add token");
}
