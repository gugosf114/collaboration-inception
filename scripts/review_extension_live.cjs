#!/usr/bin/env node
"use strict";

// Development-only end-to-end review. It loads the unpacked extension in a
// temporary Chrome profile, pairs with the real Python bridge, sends a command,
// answers an approval, and exercises background-script DOM pointing.

const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const readline = require("readline");
const { spawn } = require("child_process");

function argument(name, fallback = "") {
  const index = process.argv.indexOf(name);
  return index >= 0 && index + 1 < process.argv.length
    ? process.argv[index + 1]
    : fallback;
}

function waitForRecord(records, waiters, predicate, timeout = 20000) {
  const existing = records.find(predicate);
  if (existing) {
    return Promise.resolve(existing);
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      const index = waiters.indexOf(waiter);
      if (index >= 0) {
        waiters.splice(index, 1);
      }
      reject(new Error("Timed out waiting for the live bridge record"));
    }, timeout);
    const waiter = {
      predicate,
      resolve(value) {
        clearTimeout(timer);
        resolve(value);
      },
    };
    waiters.push(waiter);
  });
}

async function main() {
  const projectRoot = path.resolve(argument("--project", path.join(__dirname, "..")));
  const extensionRoot = path.resolve(argument("--extension", path.join(projectRoot, "extension")));
  const outputRoot = path.resolve(argument("--output", path.join(projectRoot, "runtime", "review")));
  const playwrightPath =
    process.env.PLAYWRIGHT_CORE ||
    "C:/Users/georg/Documents/GitHub/mcp-unified-automation/node_modules/playwright-core";
  const python = argument("--python", process.platform === "win32" ? "python" : "python3");
  const headed = process.argv.includes("--headed");
  const { chromium } = require(playwrightPath);
  let executablePath = argument(
    "--chrome",
    process.env.INCEPTION_EXTENSION_CHROME || "",
  );
  if (!executablePath) {
    const playwrightChrome = chromium.executablePath();
    if (fs.existsSync(playwrightChrome)) {
      executablePath = playwrightChrome;
    }
  }
  if (!executablePath && process.platform === "win32") {
    const cacheRoot = path.join(os.homedir(), ".cache", "puppeteer", "chrome");
    if (fs.existsSync(cacheRoot)) {
      for (const release of fs.readdirSync(cacheRoot).sort()) {
        const candidate = path.join(
          cacheRoot,
          release,
          "chrome-win64",
          "chrome.exe",
        );
        if (fs.existsSync(candidate)) {
          executablePath = candidate;
          break;
        }
      }
    }
  }
  if (!executablePath) {
    executablePath = process.platform === "win32"
      ? "C:/Program Files/Google/Chrome/Application/chrome.exe"
      : "/usr/bin/chromium";
  }
  fs.mkdirSync(outputRoot, { recursive: true });

  const bridgeProgram = `
import asyncio, json, os, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, os.environ["INCEPTION_PROJECT_ROOT"])
from scripts.live_bridge import LiveBridge
bridge = LiveBridge(Path(tempfile.mkdtemp(prefix="inception-extension-review-")), port=0)
def approval():
    decision = asyncio.run(bridge.request_approval(
        "codex", "command", {"command": "git push origin verified"}, timeout=30
    ))
    print(json.dumps({"approval": decision}), flush=True)
def command(value):
    print(json.dumps({"command": value}), flush=True)
    if value.startswith("/both "):
        threading.Thread(target=approval, daemon=True).start()
bridge.set_command_handler(command)
bridge.start()
print(json.dumps({"ready": True, "url": bridge.url, "code": bridge.pair_code}), flush=True)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    bridge.close()
`;
  const child = spawn(python, ["-u", "-c", bridgeProgram], {
    env: { ...process.env, INCEPTION_PROJECT_ROOT: projectRoot },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const records = [];
  const waiters = [];
  const lines = readline.createInterface({ input: child.stdout });
  lines.on("line", (line) => {
    try {
      const record = JSON.parse(line);
      records.push(record);
      for (const waiter of [...waiters]) {
        if (waiter.predicate(record)) {
          waiters.splice(waiters.indexOf(waiter), 1);
          waiter.resolve(record);
        }
      }
    } catch (_) {
      // The bridge's human-readable approval line is intentionally ignored.
    }
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString();
  });

  const targetServer = http.createServer((_request, response) => {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(`<!doctype html><title>Point target</title>
      <main><button id="exact-target" aria-label="Ship verified build">
      Ship verified build</button></main>`);
  });
  await new Promise((resolve) => targetServer.listen(0, "127.0.0.1", resolve));
  const targetPort = targetServer.address().port;

  let context;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "inception-chrome-profile-"));
  try {
    const ready = await waitForRecord(records, waiters, (record) => record.ready);
    context = await chromium.launchPersistentContext(profile, {
      executablePath,
      headless: !headed,
      ignoreDefaultArgs: ["--disable-extensions"],
      args: [
        `--disable-extensions-except=${extensionRoot}`,
        `--load-extension=${extensionRoot}`,
      ],
      viewport: { width: 420, height: 900 },
    });
    let worker = context.serviceWorkers()[0];
    if (!worker) {
      worker = await context.waitForEvent("serviceworker", { timeout: 15000 });
    }
    const extensionId = new URL(worker.url()).host;
    const panel = await context.newPage();
    await panel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
    await panel.locator("#bridgeUrl").fill(ready.url);
    await panel.locator("#pairingCode").fill(ready.code);
    await panel.locator("#pairingForm button[type=submit]").click();
    await panel.locator("#connectionText").getByText("Connected").waitFor();

    await panel.locator("#prompt").fill("extension live proof");
    await panel.locator('[data-prefix="/both"]').click();
    await waitForRecord(
      records,
      waiters,
      (record) => record.command === "/both extension live proof",
    );
    await panel.locator("#approvalCard:not([hidden])").waitFor();
    await panel.locator("#approveOnceButton").click();
    const approval = await waitForRecord(
      records,
      waiters,
      (record) => typeof record.approval === "string",
    );
    if (approval.approval !== "accept") {
      throw new Error(`Approval round trip returned ${approval.approval}`);
    }

    const target = await context.newPage();
    await target.goto(`http://127.0.0.1:${targetPort}/`);
    await target.bringToFront();
    const pointPromise = panel.evaluate(
      () => chrome.runtime.sendMessage({ type: "begin-point" }),
    );
    await target.locator("#exact-target").click();
    const point = await pointPromise;
    if (
      !point?.ok ||
      point.result?.selector !== "#exact-target" ||
      point.result?.ariaLabel !== "Ship verified build"
    ) {
      throw new Error(`Point mode returned unexpected evidence: ${JSON.stringify(point)}`);
    }

    process.stdout.write(
      JSON.stringify(
        {
          paired: true,
          command: true,
          approval: approval.approval,
          pointSelector: point.result.selector,
          pointAriaLabel: point.result.ariaLabel,
        },
        null,
        2,
      ) + "\n",
    );
  } finally {
    if (context) {
      await context.close();
    }
    await new Promise((resolve) => targetServer.close(resolve));
    child.kill();
    lines.close();
  }
  if (stderr.trim()) {
    process.stderr.write(stderr);
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
