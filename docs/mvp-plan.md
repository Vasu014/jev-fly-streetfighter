# Recording-focused MVP plan

## Goal

Produce one convincing screen recording of a complete Street Fighter III round
in which TypeSafe Jev controls one fighter and the MaleCNS/FlyBrain simulation
controls the other. The recording must show the game, each agent's current
decision, and synchronized numerical decision metrics.

The MVP is a fixed exhibition, not a general game-agent platform. It uses the
validated Alex versus Ryu, Super Art I matchup and the existing `SF3Engine`.

## Implemented foundation

- Reproducible CPython 3.10 and React/Vite setup.
- aiohttp application shell and read-only Orb portal.
- Real MAMEToolkit/MAME two-player engine.
- Fixed unattended local-versus startup and fresh-process reset.
- Independent simultaneous P1/P2 controls.
- Typed RAM observations and approximately 29.8 FPS RGB delivery.
- Bounded process/FIFO transport and opt-in real-ROM integration proof.
- Shared typed agent protocol and seven player-relative semantic actions.
- Drift-free scripted match coordinator with bounded decisions and fallback.
- Bounded local JSONL/summary telemetry with decision/result correlation.

## Essential remaining work

### 1. Jev player

- First prove the reusable adapter and presentation path with the intermediate
  [Jev-versus-Jev demo](jev-vs-jev-plan.md).
- Read current TypeSafe API and question guidance before implementing calls.
- Send named game-state fields and request typed probabilities over the seven
  legal actions.
- Select the highest-probability valid action.
- Apply bounded request deadlines and a safe fallback.
- Report the selected action, all action probabilities, confidence margin,
  latency, and timeout/error count without exposing credentials.

### 2. FlyBrain player

- Load the real MaleCNS dataset and benchmark memory, startup, and simulation
  throughput in the Orb.
- Convert the shared game state into documented sensory-neuron stimulation.
- Assign documented output-neuron groups to the seven actions.
- Run one bounded neural decision window and convert activity to action scores.
- Report the selected action, all action scores, total spikes, active neurons,
  mean and peak firing rate, and simulation latency.
- If the full model misses the decision budget, first reduce simulation work per
  decision rather than silently replacing or shrinking the biological network.

### 3. Development logging and telemetry

Assign every match, decision, and emulator observation a correlation ID. Keep a
bounded in-memory event buffer for the dashboard and write append-only JSONL per
match under an ignored local directory:

```text
data/runs/<match-id>/
├── events.jsonl
├── summary.json
└── recording.mp4
```

Each decision event must contain:

- timestamp, match/decision ID, emulator sequence, and native frame;
- normalized game-state snapshot;
- agent and player identity;
- score or probability for every available action;
- selected action and whether it was a fallback;
- decision/simulation latency and deadline status;
- Jev confidence/error category or FlyBrain activity metrics;
- controller inputs actually sent to MAME;
- resulting health, position, and damage changes.

Lifecycle events must cover emulator/model start and shutdown, match and round
transitions, reset, API timeout, stale or invalid decision, process failure, and
cleanup. Emit concise structured lifecycle/error logs to stdout and provide a
development-only endpoint or command to retrieve the latest match log.

Never log API keys, authorization headers, ROM contents, internal exception
messages, or every raw framebuffer. Logs must remain local and untracked.

The telemetry is sufficient when a visible mistake in a recording can be tied
to the exact observation, model scores, selected action, MAME input, and any
latency, stale-data, or fallback condition.

### 4. Demo screen and recording

- Display the live game prominently and label the fighters Jev and FlyBrain.
- Show health, timer, current action, and a short decision timeline.
- Show Jev probabilities, confidence, and latency numerically.
- Show FlyBrain action scores, spikes, active neurons, firing rate, and latency
  numerically.
- Clearly label fallback, timeout, and disconnected states.
- Provide trusted operator start, stop, and restart controls without enabling
  public emulator mutation routes.
- Create a presentation-sized desktop layout and capture a synchronized MP4.
- Rehearse three to five matches before recording the final complete round.

## Explicitly deferred

- Database or remote telemetry storage.
- Match history, accounts, spectators, or tournaments.
- Multiple characters, stages, or configurable matchups.
- Full six-button vocabulary, throws, parries, special moves, and combos.
- Save-state recovery or deterministic replay claims.
- Authenticated public game controls.
- Mobile layout, production autoscaling, and long endurance testing.
- A general-purpose game-agent framework.

## MVP completion gate

The MVP is complete when a reviewable MP4 shows one full Jev-versus-FlyBrain
round with both fighters visibly controlled by their assigned systems and with
synchronized numerical metrics throughout. The associated local telemetry must
explain every displayed decision and confirm clean emulator/model teardown.
