"use strict";

const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765";
let activityTimer = 0;

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

async function bridgeConnection() {
  const stored = await chrome.storage.local.get([
    "inceptionToken",
    "inceptionBridgeUrl",
  ]);
  return {
    token: stored.inceptionToken || "",
    url: stored.inceptionBridgeUrl || DEFAULT_BRIDGE_URL,
  };
}

async function bridgePost(path, body) {
  const connection = await bridgeConnection();
  const token = connection.token;
  if (!token) {
    return;
  }
  await fetch(`${connection.url}${path}`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  }).catch(() => {});
}

async function capturePoint(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: () => new Promise((resolve) => {
      const priorCursor = document.documentElement.style.cursor;
      const priorTitle = document.title;
      const banner = document.createElement("div");
      banner.textContent = "INCEPTION POINT MODE · click the exact element · Esc cancels";
      Object.assign(banner.style, {
        position: "fixed",
        zIndex: "2147483647",
        left: "50%",
        top: "18px",
        transform: "translateX(-50%)",
        background: "#101724",
        color: "#f7f3e8",
        border: "1px solid #57d9bd",
        borderRadius: "999px",
        boxShadow: "0 12px 40px rgba(0,0,0,.35)",
        font: "700 13px/1.2 system-ui, sans-serif",
        letterSpacing: ".04em",
        padding: "12px 18px",
        pointerEvents: "none",
      });
      document.documentElement.style.cursor = "crosshair";
      document.documentElement.appendChild(banner);

      let highlighted = null;
      let priorOutline = "";
      const restoreHighlight = () => {
        if (highlighted) {
          highlighted.style.outline = priorOutline;
          highlighted = null;
        }
      };
      const cleanup = () => {
        restoreHighlight();
        banner.remove();
        document.documentElement.style.cursor = priorCursor;
        document.title = priorTitle;
        document.removeEventListener("mouseover", hover, true);
        document.removeEventListener("click", choose, true);
        document.removeEventListener("keydown", cancel, true);
      };
      const hover = (event) => {
        if (!(event.target instanceof Element) || event.target === banner) {
          return;
        }
        restoreHighlight();
        highlighted = event.target;
        priorOutline = highlighted.style.outline;
        highlighted.style.outline = "3px solid #57d9bd";
      };
      const selector = (element) => {
        if (element.id) {
          return `#${CSS.escape(element.id)}`;
        }
        const parts = [];
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
          let part = current.localName;
          const stableClass = [...current.classList]
            .find((value) => /^[a-zA-Z][a-zA-Z0-9_-]{1,48}$/.test(value));
          if (stableClass) {
            part += `.${CSS.escape(stableClass)}`;
          } else if (current.parentElement) {
            const siblings = [...current.parentElement.children]
              .filter((item) => item.localName === current.localName);
            if (siblings.length > 1) {
              part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
            }
          }
          parts.unshift(part);
          current = current.parentElement;
        }
        return parts.join(" > ");
      };
      const choose = (event) => {
        if (!(event.target instanceof Element) || event.target === banner) {
          return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        const element = event.target;
        const bounds = element.getBoundingClientRect();
        const payload = {
          cancelled: false,
          selector: selector(element),
          text: (element.innerText || element.textContent || "").trim().slice(0, 1200),
          role: element.getAttribute("role") || element.localName,
          ariaLabel: element.getAttribute("aria-label") || "",
          bounds: {
            x: Math.round(bounds.x),
            y: Math.round(bounds.y),
            width: Math.round(bounds.width),
            height: Math.round(bounds.height),
          },
          nearbyDom: (element.parentElement?.outerHTML || element.outerHTML).slice(0, 4000),
          pageTitle: document.title,
          pageUrl: location.href,
        };
        cleanup();
        resolve(payload);
      };
      const cancel = (event) => {
        if (event.key !== "Escape") {
          return;
        }
        event.preventDefault();
        cleanup();
        resolve({ cancelled: true });
      };
      document.addEventListener("mouseover", hover, true);
      document.addEventListener("click", choose, true);
      document.addEventListener("keydown", cancel, true);
    }),
  });
  return results[0]?.result || { cancelled: true };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "human-activity") {
    const now = Date.now();
    if (now - activityTimer > 1200) {
      activityTimer = now;
      bridgePost("/api/human-activity", {
        source: message.source || "browser",
        tabId: sender.tab?.id,
      });
    }
    return;
  }
  if (message?.type === "begin-point") {
    (async () => {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab?.id || !/^https?:/.test(tab.url || "")) {
        throw new Error("Open an ordinary web page before pointing.");
      }
      return capturePoint(tab.id);
    })()
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
});
