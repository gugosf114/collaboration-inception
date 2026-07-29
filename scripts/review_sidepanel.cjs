#!/usr/bin/env node
"use strict";

// Development-only visual capture helper. It uses an existing playwright-core
// installation and never downloads another browser.

const path = require("path");
const fs = require("fs");
const { pathToFileURL } = require("url");

function argument(name, fallback = "") {
  const index = process.argv.indexOf(name);
  return index >= 0 && index + 1 < process.argv.length
    ? process.argv[index + 1]
    : fallback;
}

async function main() {
  const playwrightPath =
    process.env.PLAYWRIGHT_CORE ||
    "C:/Users/georg/Documents/GitHub/mcp-unified-automation/node_modules/playwright-core";
  const { chromium } = require(playwrightPath);
  const extensionRoot = path.resolve(argument("--extension", path.join(__dirname, "..", "extension")));
  const outputRoot = path.resolve(argument("--output", path.join(__dirname, "..", "runtime", "review")));
  const executablePath = argument(
    "--chrome",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
  );
  fs.mkdirSync(outputRoot, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath });
  try {
    for (const width of [360, 420]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      await page.goto(pathToFileURL(path.join(extensionRoot, "sidepanel.html")).href);
      await page.screenshot({
        path: path.join(outputRoot, `pairing-${width}.png`),
        fullPage: true,
      });
      await page.evaluate(() => {
        document.querySelector("#pairingCard").hidden = true;
        document.querySelector("#cockpit").hidden = false;
        document.querySelector("#turnState").textContent = "Codex + Claude working";
        document.querySelector("#controlOwner").textContent = "Agent";
        const stream = document.querySelector("#eventStream");
        document.querySelector("#emptyState").hidden = true;
        stream.hidden = false;
        for (const [agent, body] of [
          ["codex", "I found the failing browser handoff and am repairing the control boundary."],
          ["claude", "The bridge is sound. One consequence still needs George’s approval."],
          ["system", "Approval requested: publish the verified build."],
        ]) {
          const item = document.createElement("li");
          item.className = `event event-${agent}`;
          item.innerHTML = `<div class="event-head"><span class="event-agent">${agent}</span><time class="event-time">8:42 PM</time></div><p class="event-body"></p>`;
          item.querySelector(".event-body").textContent = body;
          stream.appendChild(item);
        }
        document.querySelector("#approvalCard").hidden = false;
        document.querySelector("#approvalTitle").textContent = "Codex requests publish";
        document.querySelector("#approvalDetail").textContent =
          JSON.stringify({ command: "git push origin main", cwd: "collaboration-inception" }, null, 2);
      });
      await page.screenshot({
        path: path.join(outputRoot, `active-${width}.png`),
        fullPage: true,
      });
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
