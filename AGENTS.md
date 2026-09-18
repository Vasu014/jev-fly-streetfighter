# Project guidance

## Mission and current scope

This repository is building a web exhibition in which TypeSafe Jev and a
MaleCNS/FlyBrain simulation control opposite players in Street Fighter III:
3rd Strike.

The implemented foundation currently includes:

- a React/Vite application shell;
- an aiohttp backend that serves the production frontend and JSON APIs;
- a real two-player `SF3Engine` using MAMEToolkit's bundled MAME;
- passive readiness reporting for MAME, FlyBrain dependencies, and TypeSafe;
- typed game observations and independent P1/P2 controls.

Jev decisions, the full MaleCNS model, the continuous match coordinator, match
storage, and the finished telemetry dashboard are later phases. Do not fake
those components or report them as implemented.

## Repository layout

- `backend/jev_fly/`: aiohttp application, health reporting, and emulator code.
- `backend/jev_fly/engine.py`: public SF3 engine contracts and lifecycle.
- `backend/jev_fly/_mame.py`: private MAME process, Lua console, and FIFO transport.
- `frontend/`: React/Vite application and frontend tests.
- `tests/`: backend unit, HTTP, lifecycle, and opt-in real-ROM integration tests.
- `docs/emulator.md`: authoritative emulator behavior, RAM provenance, security,
  timing, limitations, and upstream licensing notes.
- `.agents/setup`: idempotent Orb/toolchain setup.
- `.amp/services.yaml`: supervised read-only portal service.

Read `docs/emulator.md` before changing emulator inputs, RAM addresses, startup
sequences, timing, lifecycle, or HTTP control behavior.

## Setup and routine commands

Run from the repository root:

```bash
.agents/setup

PYTHONPATH=backend .venv/bin/pytest -q
npm --prefix frontend test
npm --prefix frontend run build

amp orb services ensure
```

The required runtime is intentionally pinned to CPython 3.10.21,
MAMEToolkit 1.1.0, Brian2 2.5.1, NumPy 1.24.0, Cython 0.29.36, and the exact
npm lockfile. Do not casually upgrade these packages: newer NumPy is known to
break the canonical FlyBrain stack, and the bundled MAME behavior is part of the
tested engine contract. Update pins only as a dedicated compatibility change
with full verification.

Generated directories such as `.venv`, `frontend/node_modules`,
`frontend/dist`, caches, recordings, NVRAM, and FIFO/runtime directories must
remain untracked.

## Architecture and implementation boundaries

- Keep the deployed shape simple: one aiohttp web/coordinator application,
  React/Vite static assets, and isolated MAME/FlyBrain child processes.
- Do not introduce Next.js, Vercel-specific infrastructure, DIAMBRA, Redis,
  PostgreSQL, Kubernetes, or a required Docker layer without an explicit product
  need.
- Keep emulator code behind the typed `SF3Engine`, `Controls`, `Button`,
  `Observation`, and `PlayerState` contracts.
- Importing modules must never start MAME, load FlyBrain, or call TypeSafe.
- Blocking emulator operations must stay off the aiohttp event loop and remain
  serialized across start, reset, stop, and step.
- Preserve the pull-driven contract until a later coordinator explicitly owns a
  continuous loop. Do not add an unowned background emulator thread.
- Actions last one sampled two-frame step. Repeating an action holds it; an
  empty step releases it.
- Do not claim deterministic replay. Reset currently means a fresh process and
  unattended fixed-match boot.
- Prefer direct, small changes over wrappers or abstractions for future phases.

## Emulator invariants

- Supported game: `sfiii3n`, fixed validated matchup Alex vs Ryu, Super Art I.
- Expected frame: owned, read-only NumPy `uint8` RGB with shape `(224, 384, 3)`.
- Sampling target: two native frames per observation, approximately 29.8 FPS.
- P2 Roundhouse Kick is `:INPUTS` in the bundled MAME 0.211 build. Do not move it
  to `:EXTRA` based on physical-harness assumptions.
- The public stun value is the high byte of the u32 accumulator (`raw >> 24`).
- Do not guess or silently change RAM addresses. Cross-check proven sources,
  update `docs/emulator.md`, and add a test that distinguishes the old and new
  interpretation.
- Startup and I/O must remain bounded. Every failure path must terminate, kill
  if needed, wait/reap the child, close descriptors, and remove private runtime
  directories.
- Never use global MAMEToolkit FIFO paths. Each engine instance owns a private
  temporary runtime directory.

## ROM and generated game data

The existing `sfiii3n.zip` was explicitly supplied for this project. Preserve
it exactly unless the owner asks to replace it. Do not print, unpack into the
repository, upload elsewhere, or include its contents in logs or test fixtures.
Do not add any other ROM, CHD, NVRAM, save-state, or downloaded parent archive.

Tests must accept the ROM through the repository path or `SFIII3_ROM_PATH` and
must skip cleanly when it is absent. Unit tests must not depend on ROM bytes.
Temporary integration-test downloads and repackaged archives must be deleted.

## Security and secrets

- Never print or return `TYPESAFE_API_KEY` or any other credential.
- Routine health checks must not make paid TypeSafe requests. Report
  configuration and unchecked reachability truthfully.
- The portal service is read-only and explicitly sets
  `SF3_ENABLE_LOCAL_CONTROL=0`.
- Mutation routes may be registered only when the exact value
  `SF3_ENABLE_LOCAL_CONTROL=1` is supplied to a separate trusted-local process.
  This flag is not authentication or CSRF protection. Never enable it on a
  public portal or untrusted network.
- API errors must not expose ROM paths, console output, secrets, or internal
  exception messages. Log or return safe exception categories instead.
- Bound request sizes and action counts before dispatching emulator work.

## Testing expectations

For backend-only changes, run the focused test first and then:

```bash
PYTHONPATH=backend .venv/bin/pytest -q
```

For frontend changes, also run:

```bash
npm --prefix frontend test
npm --prefix frontend run build
```

Inspect rendered desktop and narrow states for any visual UI change. Use the
Orb's supervised service and browser workflow rather than an ad hoc background
server.

Run the expensive real-ROM proof when changing engine behavior, MAME transport,
inputs, RAM interpretation, startup/reset, timing, or teardown:

```bash
SF3_INTEGRATION=1 PYTHONPATH=backend \
  .venv/bin/pytest -s -q tests/test_engine_integration.py
```

The real test must prove outcomes: ROM audit, local-versus fighting state,
correct fixed characters/arts, asymmetric simultaneous P1/P2 movement, P2
damage, fresh frames and timing, action release, repeated boot/reset, bounded
failure, and process/FIFO cleanup. Process survival alone is not sufficient.

After emulator tests, verify there are no leaked MAME processes,
`jev-sf3-*` runtime directories, or toolkit FIFOs. Do not hide upstream warnings
to manufacture clean output.

## Documentation and delivery

- Keep `README.md` concise and operational; put detailed emulator contracts and
  provenance in `docs/emulator.md`.
- Update health/status output and docs whenever readiness semantics change.
- Distinguish prerequisites-ready from process-running and model-loaded states.
- Preserve attribution and license notes. Do not claim legal clearance or copy
  code from unlicensed reference repositories.
- Do not commit, push, deploy, publish, or expose mutation controls unless the
  user explicitly requests that action.
