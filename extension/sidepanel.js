"use strict";

const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765";
const elements = {
  pairingCard: document.querySelector("#pairingCard"),
  pairingForm: document.querySelector("#pairingForm"),
  pairingCode: document.querySelector("#pairingCode"),
  bridgeUrl: document.querySelector("#bridgeUrl"),
  pairingError: document.querySelector("#pairingError"),
  cockpit: document.querySelector("#cockpit"),
  connectionPill: document.querySelector("#connectionPill"),
  connectionText: document.querySelector("#connectionText"),
  turnState: document.querySelector("#turnState"),
  controlOwner: document.querySelector("#controlOwner"),
  handbackButton: document.querySelector("#handbackButton"),
  emptyState: document.querySelector("#emptyState"),
  eventStream: document.querySelector("#eventStream"),
  clearButton: document.querySelector("#clearButton"),
  approvalCard: document.querySelector("#approvalCard"),
  approvalTitle: document.querySelector("#approvalTitle"),
  approvalDetail: document.querySelector("#approvalDetail"),
  denyButton: document.querySelector("#denyButton"),
  approveOnceButton: document.querySelector("#approveOnceButton"),
  approveSessionButton: document.querySelector("#approveSessionButton"),
  prompt: document.querySelector("#prompt"),
  voiceButton: document.querySelector("#voiceButton"),
  pointButton: document.querySelector("#pointButton"),
  stopButton: document.querySelector("#stopButton"),
  composerError: document.querySelector("#composerError"),
};

let token = "";
let bridgeUrl = DEFAULT_BRIDGE_URL;
let cursor = 0;
let polling = false;
let activeApproval = null;
let recognition = null;
let deltaRows = new Map();

async function storedConnection() {
  const stored = await chrome.storage.local.get([
    "inceptionToken",
    "inceptionBridgeUrl",
  ]);
  return {
    token: stored.inceptionToken || "",
    bridgeUrl: stored.inceptionBridgeUrl || DEFAULT_BRIDGE_URL,
  };
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(`${bridgeUrl}${path}`, {
    ...options,
    headers,
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch (_) {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.error || `Bridge returned HTTP ${response.status}`);
  }
  return payload;
}

function setConnected(connected) {
  elements.connectionPill.classList.toggle("pill-online", connected);
  elements.connectionPill.classList.toggle("pill-offline", !connected);
  elements.connectionText.textContent = connected ? "Connected" : "Offline";
  elements.pairingCard.hidden = connected;
  elements.cockpit.hidden = !connected;
}

function text(value) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function eventIdentity(event) {
  return event.agent || event.provider || "system";
}

function appendEvent(event) {
  if (event.type === "model.delta") {
    const key = `${event.turn_id || "turn"}:${event.agent || "model"}`;
    let row = deltaRows.get(key);
    if (!row) {
      row = createEventRow(eventIdentity(event), "", event.at);
      deltaRows.set(key, row);
      elements.eventStream.appendChild(row.item);
    }
    row.body.textContent += event.text || "";
    revealEvents();
    return;
  }
  if (event.type === "model.answer") {
    const key = `${event.turn_id || "turn"}:${event.agent || "model"}`;
    const row = deltaRows.get(key);
    if (row) {
      if (!row.body.textContent.trim()) {
        row.body.textContent = event.text || "";
      }
      deltaRows.delete(key);
      row.time.textContent = timeLabel(event.at);
      revealEvents();
      return;
    }
  }
  if (event.type === "cockpit.active") {
    updateActive(Boolean(event.active), event.agents || []);
    return;
  }
  if (event.type === "control.human") {
    elements.controlOwner.textContent = "George";
    return;
  }
  if (event.type === "control.handback") {
    elements.controlOwner.textContent = event.owner === "agent" ? "Agent" : "George";
    return;
  }
  if (event.type === "approval.requested") {
    showApproval(event);
    return;
  }
  if (event.type === "approval.resolved") {
    if (activeApproval?.id === event.id) {
      hideApproval();
    }
    createAndAppend("system", `Approval ${event.decision}: ${event.id}`, event.at);
    return;
  }
  const messages = {
    "operator.command": `George → ${event.command || ""}`,
    "voice.transcript": `Voice → ${event.text || ""}`,
    "turn.started": `${(event.agents || []).join(" + ")} started ${event.mode || "a turn"}.`,
    "turn.completed": `${(event.agents || []).join(" + ")} finished.`,
    "turn.error": event.error || "The turn failed.",
    "bridge.ready": "Live bridge connected.",
    "approval.session-grant":
      `Session approval reused for ${event.provider || "agent"} ${event.kind || "action"}.`,
  };
  if (messages[event.type]) {
    createAndAppend(eventIdentity(event), messages[event.type], event.at);
  }
}

function createEventRow(agent, bodyText, at) {
  const item = document.createElement("li");
  item.className = `event event-${agent}`;
  const head = document.createElement("div");
  head.className = "event-head";
  const who = document.createElement("span");
  who.className = "event-agent";
  who.textContent = agent === "system" ? "Inception" : agent;
  const time = document.createElement("time");
  time.className = "event-time";
  time.textContent = timeLabel(at);
  const body = document.createElement("p");
  body.className = "event-body";
  body.textContent = bodyText;
  head.append(who, time);
  item.append(head, body);
  return { item, body, time };
}

function createAndAppend(agent, body, at) {
  const row = createEventRow(agent, body, at);
  elements.eventStream.appendChild(row.item);
  revealEvents();
}

function revealEvents() {
  elements.emptyState.hidden = true;
  elements.eventStream.hidden = false;
  while (elements.eventStream.children.length > 200) {
    elements.eventStream.firstElementChild?.remove();
  }
  elements.eventStream.scrollTop = elements.eventStream.scrollHeight;
}

function timeLabel(value) {
  const date = value ? new Date(value) : new Date();
  return Number.isNaN(date.valueOf())
    ? ""
    : date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function updateActive(active, agents = []) {
  elements.turnState.textContent = active
    ? `${agents.join(" + ") || "Model"} working`
    : "Ready";
  elements.controlOwner.textContent = active ? "Agent" : "George";
}

function showApproval(approval) {
  activeApproval = approval;
  elements.approvalCard.hidden = false;
  elements.approvalTitle.textContent =
    `${approval.provider || "Agent"} requests ${approval.kind || "an action"}`;
  elements.approvalDetail.textContent = JSON.stringify(approval.detail || {}, null, 2);
  elements.approvalCard.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function hideApproval() {
  activeApproval = null;
  elements.approvalCard.hidden = true;
  elements.approvalDetail.textContent = "";
}

async function refreshState() {
  const state = await api("/api/state");
  updateActive(Boolean(state.active), state.active_agents || []);
  elements.controlOwner.textContent =
    state.control_owner === "agent" ? "Agent" : "George";
  const first = (state.pending_approvals || [])[0];
  if (first) {
    showApproval(first);
  } else {
    hideApproval();
  }
  cursor = Math.max(cursor, Number(state.event_sequence || 0) - 100);
}

async function pollEvents() {
  if (polling || !token) {
    return;
  }
  polling = true;
  try {
    while (token) {
      const payload = await api(`/api/events?after=${cursor}&timeout=20`);
      for (const event of payload.events || []) {
        cursor = Math.max(cursor, Number(event.sequence || 0));
        appendEvent(event);
      }
      setConnected(true);
    }
  } catch (error) {
    setConnected(false);
    elements.pairingError.textContent =
      `Cockpit connection ended: ${error.message}`;
  } finally {
    polling = false;
  }
}

async function sendCommand(prefix, promptOverride = null) {
  elements.composerError.textContent = "";
  const prompt = (promptOverride ?? elements.prompt.value).trim();
  if (!prompt && prefix !== "/stop") {
    elements.composerError.textContent = "Say or type the outcome first.";
    elements.prompt.focus();
    return;
  }
  const command = prefix === "/stop" ? "/stop" : `${prefix} ${prompt}`;
  await api("/api/command", {
    method: "POST",
    body: JSON.stringify({ command }),
  });
  if (prefix !== "/stop") {
    elements.prompt.value = "";
  }
}

async function resolveApproval(decision) {
  if (!activeApproval) {
    return;
  }
  await api(`/api/approvals/${activeApproval.id}`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });
  hideApproval();
}

elements.pairingForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.pairingError.textContent = "";
  try {
    const candidateUrl = elements.bridgeUrl.value.trim().replace(/\/+$/, "");
    const parsed = new URL(candidateUrl);
    if (
      parsed.protocol !== "http:" ||
      !["127.0.0.1", "localhost"].includes(parsed.hostname) ||
      parsed.pathname !== "/" ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash
    ) {
      throw new Error("Use a loopback bridge such as http://127.0.0.1:8765.");
    }
    bridgeUrl = candidateUrl;
    const payload = await api("/api/pair", {
      method: "POST",
      body: JSON.stringify({ code: elements.pairingCode.value.trim() }),
    });
    token = payload.token;
    await chrome.storage.local.set({
      inceptionToken: token,
      inceptionBridgeUrl: bridgeUrl,
    });
    setConnected(true);
    await refreshState();
    pollEvents();
  } catch (error) {
    elements.pairingError.textContent = error.message;
  }
});

document.querySelectorAll(".send-command").forEach((button) => {
  button.addEventListener("click", () => {
    sendCommand(button.dataset.prefix).catch((error) => {
      elements.composerError.textContent = error.message;
    });
  });
});

elements.prompt.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendCommand("/both").catch((error) => {
      elements.composerError.textContent = error.message;
    });
  }
});

elements.stopButton.addEventListener("click", () => {
  sendCommand("/stop").catch((error) => {
    elements.composerError.textContent = error.message;
  });
});

elements.clearButton.addEventListener("click", () => {
  elements.eventStream.replaceChildren();
  elements.eventStream.hidden = true;
  elements.emptyState.hidden = false;
  deltaRows = new Map();
});

elements.handbackButton.addEventListener("click", async () => {
  await api("/api/handback", {
    method: "POST",
    body: "{}",
  });
  elements.controlOwner.textContent = "Agent";
});

elements.denyButton.addEventListener("click", () => {
  resolveApproval("decline").catch((error) => {
    elements.composerError.textContent = error.message;
  });
});
elements.approveOnceButton.addEventListener("click", () => {
  resolveApproval("accept").catch((error) => {
    elements.composerError.textContent = error.message;
  });
});
elements.approveSessionButton.addEventListener("click", () => {
  resolveApproval("acceptForSession").catch((error) => {
    elements.composerError.textContent = error.message;
  });
});

elements.pointButton.addEventListener("click", async () => {
  elements.composerError.textContent = "";
  elements.pointButton.disabled = true;
  elements.pointButton.querySelector("span:last-child").textContent = "Click page";
  try {
    const response = await chrome.runtime.sendMessage({ type: "begin-point" });
    if (!response?.ok) {
      throw new Error(response?.error || "Point mode failed.");
    }
    if (response.result?.cancelled) {
      return;
    }
    const selector = response.result.selector;
    const request = elements.prompt.value.trim() ||
      `Inspect this element and tell me what matters: ${response.result.text || response.result.role}`;
    await sendCommand(`/browser-point ${JSON.stringify(selector)}`, request);
  } catch (error) {
    elements.composerError.textContent = error.message;
  } finally {
    elements.pointButton.disabled = false;
    elements.pointButton.querySelector("span:last-child").textContent = "Point";
  }
});

elements.voiceButton.addEventListener("click", () => {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    elements.composerError.textContent =
      "Chrome speech recognition is unavailable; use LAV or type here.";
    return;
  }
  if (recognition) {
    recognition.stop();
    return;
  }
  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = navigator.language || "en-US";
  recognition.onstart = () => {
    elements.voiceButton.setAttribute("aria-pressed", "true");
    elements.composerError.textContent = "Listening…";
  };
  recognition.onresult = (event) => {
    let heard = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      heard += event.results[index][0].transcript;
    }
    elements.prompt.value = heard.trim();
  };
  recognition.onerror = (event) => {
    elements.composerError.textContent = `Voice input: ${event.error}`;
  };
  recognition.onend = () => {
    recognition = null;
    elements.voiceButton.setAttribute("aria-pressed", "false");
    elements.composerError.textContent = "";
  };
  recognition.start();
});

(async () => {
  const stored = await storedConnection();
  token = stored.token;
  bridgeUrl = stored.bridgeUrl;
  elements.bridgeUrl.value = bridgeUrl;
  if (!token) {
    setConnected(false);
    return;
  }
  try {
    await refreshState();
    setConnected(true);
    pollEvents();
  } catch (_) {
    token = "";
    await chrome.storage.local.remove("inceptionToken");
    setConnected(false);
  }
})();
