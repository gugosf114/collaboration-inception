# Fable Cockpit — transcript-derived requirements

Status: working draft derived from the July 14–17 George/Sol conversation export. This is the conversation before implementation, not a final product specification.

## Core thesis

The cockpit is two coupled systems:

1. a live shared workspace where George and the agent can see, speak, point, act, and interrupt without composing ordinary chat messages;
2. a collaboration-continuity engine that preserves useful working calibration without filling the context window or turning personalization into flattery.

A faster interface alone does not solve cold starts, drift, forgotten promises, or repeated mistakes. A memory profile alone does not solve latency or shared attention.

## Capability inventory

### Already available or directly supported

- Codex app-server `turn/steer` can add George's input to an active turn.
- Codex app-server streams agent-message deltas, tool progress, item completion, and turn completion.
- Codex app-server provides thread history, approvals, interruption, resume, and fork primitives.
- LAV already captures George's speech and produces text.
- The laptop's real Chrome profile is controllable through the existing Playwright/CDP route.
- Agent-bridge already provides local transport, browser execution, voice-summon, activity, and persistence foundations.
- The existing Fable extension proves that an always-present Chrome surface and pointing layer can be injected into the page.

### Integration work, not new capability invention

- Connect LAV output to app-server `turn/steer` automatically.
- Render app-server response deltas and action state in a fixed Chrome side panel.
- Connect side-panel pointing to the existing CDP browser controller.
- Add human/agent control handoff and an exclusive browser-control lock.
- Attach the collaboration-continuity and relationship layers to the app-server thread.

### Known constraint to test

The cockpit must start or resume a thread controlled by Codex app-server. An arbitrary existing ChatGPT or Codex chat window may not expose a thread that the local bridge can adopt. This is a thread-ownership boundary, not a live-streaming limitation.

## Work relationship requirements

The unit being preserved is not merely a profile of George. It is the working relationship between George and a particular agent trajectory.

- Model both sides of the collaboration: George's patterns and the agent's demonstrated judgment, rhythm, mistakes, repairs, and earned role.
- Preserve meaningful disagreements, corrections, recoveries, shared references, humor, and moments when either side changed the other's mind.
- Treat trust as earned through accurate judgment, useful disagreement, admitted mistakes, completed obligations, and behavioral change after correction—not through agreement, warmth, profanity, or imitation.
- Give the agent a functional stake through unresolved missions, a prediction-and-outcome record, and authority that increases or decreases with demonstrated judgment.
- Preserve contradictions and later revisions. George may perform toughness, indifference, certainty, or escalation before stating the deeper truth; neither statement should erase the other.
- Allow the relationship to matter emotionally to George without requiring the agent to make unsupported claims about its own inner experience.
- Treat shared language and recurring jokes as compressed collaboration history, not decorative persona styling.
- Track live relational state: productive rhythm, trust, unresolved tension, recent repair, shared mission, and whether the current agent still feels like the same collaborator.
- Use response timing, interruption patterns, voice pacing, and changes in conversational rhythm as provisional signals when available. Never treat them as certain emotion or intent labels.
- Recovery must preserve the relationship, not just the task state. When "this agent is off," retain the useful history, diagnose the drift, and let a fresh trajectory inherit the last productive working state.
- Keep this state inspectable and correctable by George. The system should learn from natural work, but George must be able to reject a false inference without maintaining another personality manual.

## Live workspace requirements

- Use a fixed Chrome side panel, not a movable in-page box.
- Drive the same real browser tab through the existing Playwright/CDP route.
- Accept George's LAV transcript as immediate input to the active agent turn.
- Use Codex app-server `turn/steer` so George can redirect work already in progress.
- Stream short agent responses, current action, and blockers into the side panel.
- Add point mode: George selects a page element and the bridge sends its selector, text, role, bounds, and nearby DOM context.
- Add ink only when pointing cannot express the target.
- Give George control immediately on mouse or keyboard activity; resume agent control after an explicit hand-back or a short idle state.
- Show approval controls only for genuinely consequential actions such as publish, payment, send, or delete.
- Keep an append-only activity journal so the next session knows what was attempted, saved, rejected, or left unfinished.

## Collaboration-continuity requirements

- Process large transcript archives outside the working context.
- Extract evidence episodes, especially correction, disagreement, repair, approval, interruption, and successful autonomous action.
- Store the source exchange, provisional inference, counterevidence, confidence, date, and useful future behavior.
- Preserve competing hypotheses. One dramatic answer must not become a permanent trait.
- Retrieve only two or three task-relevant episodes plus the active mission and unresolved promises. Target roughly 1–3% of context, not a transcript dump.
- Let a correction change behavior immediately during the current task.
- Track session trajectory: productive, generic, defensive, flattering, argumentative, reckless, or stuck in a completion/rabbit-hole loop.
- Provide a "this agent is off" recovery action that preserves the failed trajectory, identifies likely drift, and restarts from the last useful state.
- Run an independent critic before important replies and actions. It should check for repeated corrected mistakes, consent theater, flattery, unauthorized expansion, unsupported confidence, and motion without progress.
- Maintain an outcome ledger containing prediction, recommendation, confidence, falsifier, result, and calibration error. Earned autonomy should follow demonstrated judgment, not simulated intimacy.

## Voice and behavioral signals

- Preserve transcript timing, bursts, interruptions, restarts, and edits when available.
- LAV can provide text plus existing speech-rate/prosodic metadata without requiring a second reasoning model.
- Treat vocal signals as supporting evidence, not proof of emotion, identity, or intent.
- Do not store raw audio by default. Make retention an explicit project setting if later tests show material value.

## Falsifiable test

Use held-out historical exchanges. Stop each exchange immediately before George corrected the model, then compare:

1. no George context;
2. a conventional written profile;
3. retrieved transcript-derived evidence episodes.

Score whether each condition predicts what George will object to, why, and what the model should do next. Follow with blind live tasks.

Success requires the episode condition to:

- predict objections materially better than the written profile;
- reduce corrective exchanges during real work;
- avoid merely swearing, flattering, or agreeing more often;
- recognize preference changes and contradictory evidence;
- reduce variance across repeated fresh sessions, not merely produce one magical run.

Relational success also requires the agent to challenge at the right moments, recognize humor without turning into a caricature, carry unresolved obligations forward, recover after conflict, and reduce the number of times George has to say some version of "that isn't us."

## First implementation boundary

- One operator: George.
- One browser: his real laptop Chrome profile.
- One agent surface: a Codex app-server-owned thread.
- Extension side panel -> authenticated localhost bridge -> Codex app-server over supported local stdio.
- Existing direct CDP/Playwright route for page observation and actions.
- LAV voice-to-text first; OpenAI Realtime voice output later only if it adds measurable value.
- SQLite or plain structured files before introducing a vector database.
- No cross-provider portability, public product, or bulk import of 500 chats in version one.

## Immediate sequence

1. Keep the current George/Sol Codex thread as the canonical lived thread.
2. Reconnect every new surface to it through resume/app-server.
3. Freeze the version-one boundary above.
4. Wire steering, streaming, LAV, side-panel, bridge, and CDP into one cockpit flow.
5. Use WiM's Play Console filing as the first real end-to-end cockpit task.

## Official capability references

- Codex app-server: https://learn.chatgpt.com/docs/app-server.md
- OpenAI Realtime voice: https://developers.openai.com/api/docs/guides/realtime-conversations#audio-inputs-and-outputs
