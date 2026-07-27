#!/usr/bin/env node
"use strict";

// Capture the same real Chrome tab the operator is using. Chrome must expose a
// CDP port; the default is the loopback-only 127.0.0.1:9222.

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

async function connectTarget(cdp, needle) {
  if (typeof WebSocket !== "function") {
    throw new Error("Browser capture needs Node.js 22 or newer");
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
    throw new Error("Chrome has no capturable page");
  }
  let target = pages[0];
  if (needle) {
    const match = pages.find(
      (item) =>
        item.url.toLowerCase().includes(needle) ||
        item.title.toLowerCase().includes(needle),
    );
    if (match) {
      target = match;
      await fetch(`${cdp}/json/activate/${target.id}`).catch(() => {});
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  } else {
    const activeTitle = comparableTitle(activeWindowsBrowserTitle());
    const active = pages.find((item) => {
      const title = comparableTitle(item.title);
      return title.length >= 4 && activeTitle.includes(title);
    });
    if (active) {
      target = active;
    } else {
      const probes = await Promise.all(pages.map((page) => probePage(page)));
      target =
        probes.find((probe) => probe.visibility === "visible")?.page ||
        probes.find((probe) => probe.visibility)?.page ||
        target;
    }
  }
  const socket = new WebSocket(target.webSocketDebuggerUrl);
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
  return { pages, target, socket, send };
}

function windowsBrowserScreenshot() {
  const script = String.raw`
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class InceptionWindow {
  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int command);
}
"@
$handle = [InceptionWindow]::GetForegroundWindow()
$process = Get-Process | Where-Object { $_.MainWindowHandle -eq $handle } | Select-Object -First 1
if ($null -eq $process -or $process.ProcessName -notmatch "^(chrome|msedge)$") {
  $process = Get-Process chrome,msedge -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle } |
    Sort-Object StartTime -Descending |
    Select-Object -First 1
  if ($null -eq $process) { throw "No visible Chrome or Edge window was found." }
  $handle = [IntPtr]$process.MainWindowHandle
  [void][InceptionWindow]::ShowWindow($handle, 9)
  [void][InceptionWindow]::SetForegroundWindow($handle)
  Start-Sleep -Milliseconds 350
}
$rect = New-Object InceptionWindow+RECT
if (-not [InceptionWindow]::GetWindowRect($handle, [ref]$rect)) {
  throw "Cannot read the browser window bounds."
}
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -lt 100 -or $height -lt 100) { throw "The browser window is minimized." }
$bitmap = New-Object Drawing.Bitmap $width, $height
$graphics = [Drawing.Graphics]::FromImage($bitmap)
$stream = New-Object IO.MemoryStream
try {
  $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
  $bitmap.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
  [pscustomobject]@{
    pngBase64 = [Convert]::ToBase64String($stream.ToArray())
    title = $process.MainWindowTitle
    bounds = [pscustomobject]@{
      x = $rect.Left; y = $rect.Top; width = $width; height = $height
    }
  } | ConvertTo-Json -Compress
}
finally {
  $stream.Dispose()
  $graphics.Dispose()
  $bitmap.Dispose()
}
`;
  const encoded = Buffer.from(script, "utf16le").toString("base64");
  const output = execFileSync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
    { encoding: "utf8", timeout: 30000, windowsHide: true },
  ).trim();
  return JSON.parse(output);
}

async function main() {
  const bridge = bridgeConfiguration();
  const output = argument("--output");
  const metadata = argument("--metadata");
  const target = String(bridge?.target || argument("--target")).toLowerCase();
  if (!bridge && (!output || !metadata)) {
    throw new Error("Use --output IMAGE --metadata JSON [--target URL_OR_TITLE]");
  }
  const cdp = process.env.CDP_URL || "http://127.0.0.1:9222";
  const connection = await connectTarget(cdp, target);
  try {
    let pngBase64;
    let fallback = null;
    try {
      const screenshot = await connection.send("Page.captureScreenshot", {
        format: "png",
        fromSurface: true,
        captureBeyondViewport: false,
      });
      pngBase64 = screenshot.data;
    } catch (error) {
      if (process.platform !== "win32") {
        throw error;
      }
      await fetch(`${cdp}/json/activate/${connection.target.id}`).catch(() => {});
      fallback = windowsBrowserScreenshot();
      pngBase64 = fallback.pngBase64;
    }
    const detail = {
      adapter: fallback
        ? bridge
          ? "remote-windows-browser"
          : "windows-browser"
        : bridge
          ? "remote-cdp"
          : "cdp",
      cdp,
      url: connection.target.url,
      title: fallback?.title || connection.target.title,
      pageCount: connection.pages.length,
      ...(fallback?.bounds ? { bounds: fallback.bounds } : {}),
    };
    if (bridge) {
      process.stdout.write(
        JSON.stringify({ pngBase64, metadata: detail }),
      );
    } else {
      fs.writeFileSync(output, Buffer.from(pngBase64, "base64"));
      fs.writeFileSync(metadata, JSON.stringify(detail, null, 2));
    }
  } finally {
    connection.socket.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
