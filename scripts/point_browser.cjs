#!/usr/bin/env node
"use strict";

// Find one element in the current real Chrome tab, outline it, and preserve the
// element's text/role/bounds/nearby DOM context beside the screenshot.

const fs = require("fs");
const { execFileSync } = require("child_process");

function argument(name, fallback = "") {
  const index = process.argv.indexOf(name);
  return index >= 0 && index + 1 < process.argv.length
    ? process.argv[index + 1]
    : fallback;
}

function bridgeConfiguration() {
  const encoded = argument("--bridge-config");
  if (!encoded) {
    return null;
  }
  try {
    return JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
  } catch (error) {
    throw new Error(`Invalid remote bridge configuration: ${error.message}`);
  }
}

function activeWindowsBrowserTitle() {
  if (process.platform !== "win32") {
    return "";
  }
  try {
    const script = String.raw`
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class InceptionForeground {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@
$handle = [InceptionForeground]::GetForegroundWindow()
(Get-Process | Where-Object { $_.MainWindowHandle -eq $handle } |
  Select-Object -First 1 -ExpandProperty MainWindowTitle)
`;
    const encoded = Buffer.from(script, "utf16le").toString("base64");
    return execFileSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encoded,
      ],
      { encoding: "utf8", timeout: 10000, windowsHide: true },
    ).trim();
  } catch (_) {
    return "";
  }
}

function comparableTitle(value) {
  return String(value)
    .replaceAll("&#39;", "'")
    .replaceAll("&amp;", "&")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function probePage(page) {
  return new Promise((resolve) => {
    const socket = new WebSocket(page.webSocketDebuggerUrl);
    let finished = false;
    const finish = (visibility = "") => {
      if (finished) {
        return;
      }
      finished = true;
      clearTimeout(timeout);
      socket.close();
      resolve({ page, visibility });
    };
    const timeout = setTimeout(() => finish(), 2500);
    socket.addEventListener(
      "open",
      () => {
        socket.send(
          JSON.stringify({
            id: 1,
            method: "Runtime.evaluate",
            params: {
              expression: "document.visibilityState",
              returnByValue: true,
            },
          }),
        );
      },
      { once: true },
    );
    socket.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.id === 1) {
          finish(message.result?.result?.value || "");
        }
      } catch (_) {
        finish();
      }
    });
    socket.addEventListener("error", () => finish(), { once: true });
  });
}

async function connectTarget(cdp, pageNeedle = "") {
  if (typeof WebSocket !== "function") {
    throw new Error("Browser pointing needs Node.js 22 or newer");
  }
  const response = await fetch(`${cdp}/json/list`);
  if (!response.ok) {
    throw new Error(`Chrome CDP returned HTTP ${response.status}`);
  }
  const pages = (await response.json()).filter(
    (item) =>
      item.type === "page" &&
      item.webSocketDebuggerUrl &&
      !item.url.startsWith("chrome-extension://") &&
      !item.url.startsWith("devtools://"),
  );
  if (!pages.length) {
    throw new Error("Chrome has no page to point at");
  }
  let page = pages[0];
  if (pageNeedle) {
    const needle = pageNeedle.toLowerCase();
    const selected = pages.find(
      (item) =>
        item.url.toLowerCase().includes(needle) ||
        item.title.toLowerCase().includes(needle),
    );
    if (!selected) {
      throw new Error(`No browser tab matched ${JSON.stringify(pageNeedle)}`);
    }
    page = selected;
    await fetch(`${cdp}/json/activate/${page.id}`).catch(() => {});
    await new Promise((resolve) => setTimeout(resolve, 250));
  } else {
    const activeTitle = comparableTitle(activeWindowsBrowserTitle());
    const active = pages.find((item) => {
      const title = comparableTitle(item.title);
      return title.length >= 4 && activeTitle.includes(title);
    });
    if (active) {
      page = active;
    } else {
      const probes = await Promise.all(
        pages.map((candidate) => probePage(candidate)),
      );
      page =
        probes.find((probe) => probe.visibility === "visible")?.page ||
        probes.find((probe) => probe.visibility)?.page ||
        page;
    }
  }
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  const pending = new Map();
  let nextId = 1;
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    const waiting = pending.get(message.id);
    if (!waiting) {
      return;
    }
    pending.delete(message.id);
    clearTimeout(waiting.timeout);
    if (message.error) {
      waiting.reject(new Error(JSON.stringify(message.error)));
    } else {
      waiting.resolve(message.result);
    }
  });
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error("Timed out connecting to the Chrome tab")),
      10000,
    );
    socket.addEventListener(
      "open",
      () => {
        clearTimeout(timeout);
        resolve();
      },
      { once: true },
    );
    socket.addEventListener(
      "error",
      () => {
        clearTimeout(timeout);
        reject(new Error("Chrome rejected the tab connection"));
      },
      { once: true },
    );
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = nextId++;
      const timeout = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`Chrome timed out during ${method}`));
      }, 15000);
      pending.set(id, { resolve, reject, timeout });
      socket.send(JSON.stringify({ id, method, params }));
    });
  return { page, pages, socket, send };
}

async function main() {
  const bridge = bridgeConfiguration();
  const output = argument("--output");
  const metadata = argument("--metadata");
  const target = String(bridge?.target || argument("--target"));
  const pageTarget = String(
    bridge?.pageTarget || argument("--page-target"),
  ).trim();
  if ((!bridge && (!output || !metadata)) || !target) {
    throw new Error("Use --output IMAGE --metadata JSON --target TEXT_OR_SELECTOR");
  }
  const cdp = process.env.CDP_URL || "http://127.0.0.1:9222";
  const connection = await connectTarget(cdp, pageTarget);
  try {
    const expression = `(() => {
      const target = ${JSON.stringify(target)};
      const visible = (node) => {
        if (!(node instanceof Element)) return false;
        const style = getComputedStyle(node);
        const box = node.getBoundingClientRect();
        return box.width > 0 && box.height > 0 &&
          style.display !== "none" && style.visibility !== "hidden";
      };
      let element = null;
      if (target.startsWith("css=")) {
        try { element = document.querySelector(target.slice(4)); } catch (_) {}
      } else {
        const preferred = Array.from(document.querySelectorAll(
          "button,a,input,textarea,select,label,[role],[aria-label],[title]"
        ));
        const all = preferred.concat(
          Array.from(document.querySelectorAll("body *")).slice(0, 5000)
        );
        const normalized = target.trim().toLowerCase();
        const words = (node) => [
          node.innerText || node.textContent || "",
          node.getAttribute("aria-label") || "",
          node.getAttribute("title") || "",
          node.getAttribute("placeholder") || ""
        ].join(" ").replace(/\\s+/g, " ").trim().toLowerCase();
        element = all.find((node) => visible(node) && words(node) === normalized);
        if (!element) {
          element = all.find(
            (node) => visible(node) && words(node).includes(normalized)
          );
        }
        if (!element) {
          try { element = document.querySelector(target); } catch (_) {}
        }
      }
      if (!visible(element)) return null;
      element.scrollIntoView({ block: "center", inline: "center" });
      const box = element.getBoundingClientRect();
      document.getElementById("__inception_point_overlay__")?.remove();
      const overlay = document.createElement("div");
      overlay.id = "__inception_point_overlay__";
      Object.assign(overlay.style, {
        position: "fixed",
        left: (box.x - 8) + "px",
        top: (box.y - 8) + "px",
        width: (box.width + 16) + "px",
        height: (box.height + 16) + "px",
        border: "6px solid #ff1744",
        borderRadius: "12px",
        boxSizing: "border-box",
        pointerEvents: "none",
        zIndex: "2147483647",
        boxShadow: "0 0 0 4px rgba(255,255,255,.9)"
      });
      const horizontal = document.createElement("div");
      Object.assign(horizontal.style, {
        position: "absolute", left: "-24px", right: "-24px", top: "50%",
        height: "4px", background: "#ff1744"
      });
      const vertical = document.createElement("div");
      Object.assign(vertical.style, {
        position: "absolute", top: "-24px", bottom: "-24px", left: "50%",
        width: "4px", background: "#ff1744"
      });
      overlay.append(horizontal, vertical);
      document.documentElement.appendChild(overlay);
      const parent = element.parentElement;
      return {
        tag: element.tagName.toLowerCase(),
        id: element.id || "",
        role: element.getAttribute("role") || "",
        ariaLabel: element.getAttribute("aria-label") || "",
        text: (element.innerText || element.textContent || "").trim().slice(0, 1000),
        nearbyText: (
          (parent && (parent.innerText || parent.textContent)) || ""
        ).trim().slice(0, 2000),
        bounds: {
          x: box.x, y: box.y, width: box.width, height: box.height
        }
      };
    })()`;
    const evaluated = await connection.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
      userGesture: true,
    });
    if (evaluated.exceptionDetails) {
      throw new Error(
        evaluated.exceptionDetails.exception?.description ||
          evaluated.exceptionDetails.text ||
          "Browser element lookup failed",
      );
    }
    const detail = evaluated.result?.value;
    if (!detail) {
      throw new Error(`No visible browser element matched ${JSON.stringify(target)}`);
    }
    const screenshot = await connection.send("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: false,
    });
    await connection
      .send("Runtime.evaluate", {
        expression:
          'document.getElementById("__inception_point_overlay__")?.remove()',
      })
      .catch(() => {});
    const result = {
      adapter: bridge ? "remote-cdp-element" : "cdp-element",
      cdp,
      url: connection.page.url,
      title: connection.page.title,
      target,
      ...detail,
    };
    if (bridge) {
      process.stdout.write(
        JSON.stringify({ pngBase64: screenshot.data, metadata: result }),
      );
    } else {
      fs.writeFileSync(output, Buffer.from(screenshot.data, "base64"));
      fs.writeFileSync(metadata, JSON.stringify(result, null, 2));
    }
  } finally {
    await connection
      .send("Runtime.evaluate", {
        expression:
          'document.getElementById("__inception_point_overlay__")?.remove()',
      })
      .catch(() => {});
    connection.socket.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
