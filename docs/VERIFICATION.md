# Verification record

## 2026-07-28 capability-ceiling build

Automated verification:

- Termux: 107 tests ran; 105 passed and 2 native-Windows installer tests
  skipped.
- Native Windows: the exact working tree ran all 107 tests; 103 passed and 4
  Unix-only launcher tests skipped.
- Every platform-specific installer test passed on its native platform.
- Every Python source file compiled.
- Node syntax checks passed for both browser capture scripts, the side-panel
  review helper, and all three extension scripts.
- The extension manifest parsed as JSON.
- `git diff --check` reported no whitespace errors.

Real provider proof:

- Claude Opus 4.8 and GPT-5.6 Sol connected through the same cockpit.
- Both independently received and repeated nonce `CI-LIVE-20260728`.
- One bounded cross-model challenge completed and returned control to George.
- The authenticated live bridge paired while that cockpit was running.
- A `/status` command submitted through the bridge executed in the terminal.
- Both provider turns finished cleanly.

Browser proof:

- The pairing and active cockpit states were rendered in real Chrome at 360px
  and 420px side-panel widths.
- The unpacked Manifest V3 extension was loaded in an isolated real-Chrome
  profile. Its service worker and side-panel page paired with the Python bridge,
  submitted `/both extension live proof`, displayed and resolved a real
  blocking approval, and captured `#exact-target` with the expected ARIA label
  through point mode.
- The active state included live Codex, Claude, and system events; a
  consequential-action approval; voice and pointing controls; interruption;
  model routing; and control hand-back.
- A transcript/composer overlap found in the first capture was repaired and the
  corrected 420px state was rendered again.

GitHub Actions repeats compile, JavaScript, manifest, and unit checks on Ubuntu
and native Windows for every push and pull request.
