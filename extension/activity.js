"use strict";

let lastSent = 0;
function noteActivity(event) {
  const now = Date.now();
  if (now - lastSent < 1000) {
    return;
  }
  lastSent = now;
  chrome.runtime.sendMessage({
    type: "human-activity",
    source: event.type === "keydown" ? "keyboard" : "pointer",
  }).catch(() => {});
}

document.addEventListener("pointerdown", noteActivity, true);
document.addEventListener("keydown", noteActivity, true);
