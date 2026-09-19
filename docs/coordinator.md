# Phase 3 scripted match coordinator

`jev_fly.match.MatchCoordinator` is the single owner of a continuous,
pull-driven `SF3Engine` match. Calling `run()` starts the engine, samples one
two-native-frame observation per step, requests both decisions from the same
immutable boundary snapshot every 500 ms, and closes the engine on completion,
failure, cancellation, or the three-minute match timeout. Imports do not start
MAME. The aiohttp application and frontend are intentionally unchanged in this
phase, and no coordinator mutation route is exposed.

## Shared protocol

`GameSnapshot` contains only proven state: fighting, timer, health, world X/Y,
absolute distance, damage since the prior sample, and each player's previous
semantic action. It labels P1 and P2 explicitly. Each `DecisionRequest` adds the
agent's `Player` perspective while retaining the exact same snapshot object for
both agents.

The seven actions are neutral, advance, retreat, block, jump, light attack, and
heavy attack. They compile to no input, toward, away, away, Up, Jab, and
Roundhouse respectively. The coordinator holds the selected semantic action
until the next decision but recompiles it against current positions before
every two-frame engine step, so advance, retreat, and block reverse after a side
crossing. P1 and P2 controls are sent in one `Controls` value.

The default decision deadline is 400 ms. Decisions carry match, decision,
observation, player, agent, score, and monotonic timing identity. Missing, late,
invalid, or stale decisions are rejected and that player blocks. Pending agent
tasks are cancelled and awaited during bounded cleanup.

`AggressiveAgent` and `DefensiveAgent` are deterministic Phase 3 validation
agents implementing the same async `Agent` protocol intended for later real
adapters. They are not Jev or FlyBrain implementations.

## Telemetry

`MatchTelemetry` keeps a bounded in-memory ring and writes local append-only
events to `data/runs/<match-id>/events.jsonl`, followed by `summary.json`.
Decision events correlate the boundary observation and first resulting
observation with all seven scores, selected/applied action, fallback and
deadline status, coordinator-measured latency, the exact pre-control snapshot,
simultaneous controls, health, position, and damage. Lifecycle events include
match/emulator/round transitions, timeout, stale or failed decisions, process
failure, cancellation, match completion, telemetry write failure, and cleanup.

Persistence failures add a safe event to the ring and structured stdout without
including exception messages. The coordinator continues using bounded memory
and still performs agent and engine cleanup. Frames, ROM data, paths, and
credentials are never recorded.
