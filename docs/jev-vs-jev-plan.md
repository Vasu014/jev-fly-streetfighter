# Phase 4 Jev-versus-Jev demo plan

## Outcome

Run one bounded Alex-versus-Ryu match in which two independently configured
TypeSafe Jev agents use the Phase 3 `Agent` protocol, then present the live game
and synchronized decision telemetry in a read-only spectator page.

This is an intermediate proof for the eventual Jev-versus-FlyBrain exhibition.
It must reuse the shared coordinator and action vocabulary rather than create a
Jev-specific game loop.

## Existing foundation

- `MatchCoordinator` owns the continuous two-frame sampling loop, requests both
  decisions concurrently from the same immutable boundary snapshot, rejects
  late or stale results, applies simultaneous controls, and closes MAME on every
  exit path.
- `Agent`, `DecisionRequest`, and `Decision` define the adapter boundary.
- `MatchTelemetry` correlates snapshots, scores, controls, and resulting state
  in a bounded ring plus per-match JSONL and summary files.
- The aiohttp application already serves a latest-frame snapshot, but it owns a
  separate request-driven engine. It must not run beside a coordinator that
  controls another engine in the same demo process.
- The current frontend is a readiness shell, not a live match view.

## TypeSafe API contract

Use the official asynchronous Python SDK and one `Choice` question. The Choice
criteria are the seven legal action names and explicit descriptions; the answer
contains the selected option, a full probability distribution, and confidence.

- API: `POST /v1/systemone`
- Client: `AsyncTypeSafeClient`
- Model: configure explicitly, initially `jev-1.13.0`
- SDK baseline: `typesafe-sdk==0.7.0`, followed by an exact compiled dependency
  lock compatible with CPython 3.10 and the existing scientific pins
- Retries: `RetryPolicy(max_retries=0)`; SDK backoff must not outlive the
  coordinator's 400 ms decision deadline
- Timeout: derive each call's timeout from the remaining `DecisionRequest`
  deadline with a small cleanup margin

Production API calls are part of the acceptance proof. Keep unit and failure
tests mocked, then run a bounded schema/latency smoke test and real matches.
Health checks remain passive and never make a paid request.

## Work packages

### 1. Reproducible SDK integration

- Pin the SDK and its exact transitive dependency closure in `requirements.txt`.
- Keep `TYPESAFE_API_KEY`, `TYPESAFE_BASE_URL`, and model selection in the
  environment; never copy credentials into config, telemetry, or fixtures.
- Keep SDK debug logging disabled because request and response bodies are not
  redacted. Do not log authorization headers or exception messages.
- Verify `.agents/setup`, the full Python suite, and the canonical Brian2/NumPy
  imports after adding the dependency graph.

### 2. Reusable Jev adapter

Add a small adapter module that implements the existing async `Agent` protocol.

- Serialize a player-relative state with named fields: fighting state, timer,
  distance, own/opponent health and world position, recent damage, and previous
  semantic actions. Do not send frames or invent RAM fields.
- Ask one atomic Choice question over neutral, advance, retreat, block, jump,
  light attack, and heavy attack. Give every option criteria that distinguishes
  it from the others.
- Validate that the response contains exactly seven finite, non-negative
  probabilities. Select the highest-probability valid action in deterministic
  action order rather than trusting an unchecked response label.
- Return the complete probability map through `Decision.scores`.
- Categorize failures safely as authentication, rate limit, overload, timeout,
  connection, invalid response, or service failure. Never propagate provider
  response bodies or exception text into logs, telemetry, or HTTP responses.

### 3. Two Jev personalities and bounded spend

Instantiate the same adapter twice against one shared async client:

- `jev-rushdown`: close distance, sustain pressure, and attack in range.
- `jev-counter`: preserve spacing, block under pressure, and counterattack.

Only question guidance and stable agent identity differ. Both agents receive
the same canonical snapshot with explicit P1/P2 perspective.

Add a per-match request budget and counters for requests, accepted decisions,
timeouts, fallbacks, and provider errors. The default 500 ms cadence can issue
four requests per second across both players, so match duration and request
count must remain bounded independently.

### 4. Decision metadata and telemetry

Extend the generic decision contract only enough to carry safe adapter metrics:

- returned model identifier;
- provider confidence;
- top-versus-second probability margin;
- input token usage;
- coordinator-observed latency;
- safe error category and cumulative counters.

Persist these fields alongside the existing observation, complete scores,
selected/applied action, actual controls, fallback reason, and resulting state.
Include aggregate request, timeout, fallback, error, token, and write-failure
counts in `summary.json`.

### 5. One explicitly owned demo runtime

Add an operator-run Jev-versus-Jev composition command. It owns exactly one
`SF3Engine`, one `MatchCoordinator`, one shared TypeSafe client, both agents,
telemetry, and the read-only web runtime.

- Validate API-key and ROM configuration before starting MAME.
- Start only after an explicit operator command; imports and health checks stay
  side-effect free.
- Cancel and await decision tasks, close the SDK client, close/reap MAME, finish
  telemetry, and remove private runtime files on normal completion, signals,
  API failure, browser disconnect, and cancellation.
- Do not enable the existing public mutation routes. Start/stop remains an
  operator-side process action for this phase.

The existing aiohttp factory currently constructs its own engine. Refactor
ownership or add a focused demo composition factory so the spectator endpoints
read the coordinator-owned engine rather than creating a second controller.

### 6. Minimal read-only spectator page

Expose only the data required to watch the demonstration:

- latest RGB frame;
- match state, timer, health, winner, and agent labels;
- recent decision events and current probabilities;
- confidence, probability margin, latency, deadline/fallback state, and safe
  error counters.

Update the React page to show the live game prominently with `Jev Rushdown` and
`Jev Counter` decision panels. Polling is sufficient; do not add WebSockets
unless measured behavior requires them. Do not add public match controls,
accounts, history, or a general dashboard framework.

## Verification sequence

1. Unit tests with a fake TypeSafe client for state serialization, personality
   guidance, all seven criteria, deterministic selection, malformed responses,
   safe error mapping, remaining-deadline timeout, cancellation, and request
   budget exhaustion.
2. Coordinator tests proving the two Jev calls receive the same snapshot,
   execute concurrently, preserve distinct perspective/agent IDs, and degrade
   independently to block without stopping the other player.
3. Telemetry tests proving complete probabilities and Jev metrics correlate to
   the applied controls/result, summaries count usage correctly, and no secret,
   provider body, or internal exception text is emitted.
4. aiohttp and frontend tests for read-only status/events/frame behavior,
   loading/disconnected/fallback states, and absence of mutation routes.
5. A small production API smoke test that validates the live response schema,
   model identity, latency, and cancellation within the configured deadline.
6. Full backend tests, frontend tests/build, and inspected desktop and narrow
   browser renders.
7. An opt-in real-ROM Jev-versus-Jev match proving visible movement, attacks,
   damage, at least one completed round, accepted non-fallback Jev decisions,
   20–30 observations per second without accumulated drift, aligned telemetry,
   bounded spend, clean shutdown, and no leaked MAME processes, runtime
   directories, FIFOs, HTTP tasks, or SDK client resources.

## Demo-ready gate

The milestone is complete when a reviewable live page shows Jev Rushdown and
Jev Counter complete at least one full round while their seven probabilities,
selected/applied actions, confidence, latency, and fallback state remain
synchronized with the game. The associated local telemetry must explain every
displayed decision and confirm bounded API use and clean teardown.

## Not in this phase

- MaleCNS/FlyBrain loading or simulation
- changes to the fixed Alex/Ryu Super Art I matchup
- additional moves, combos, throws, parries, or special attacks
- database or remote telemetry storage
- authenticated public controls, accounts, spectators, or match history
- mobile polish, tournament support, or a general agent framework
