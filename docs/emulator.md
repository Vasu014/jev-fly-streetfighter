# SF3Engine: local-versus contract

The supported runtime is CPython 3.10, MAMEToolkit 1.1.0 and its bundled modified
MAME 0.211 binary, game `sfiii3n`. No ROM is distributed. Supply `sfiii3n.zip`
at the repository root or set `SFIII3_ROM_PATH`. The archive must contain the
no-CD BIOS and SIMM files at its root, not a nested `sfiii3n/` directory.

## Python and HTTP ownership

```python
from pathlib import Path
from jev_fly.engine import SF3Engine, Controls, Button

with SF3Engine(Path("sfiii3n.zip")) as engine:
    obs = engine.step(Controls({Button.RIGHT}, {Button.UP}))
    # Repeating the action on subsequent steps holds it; an empty step releases.
    print(obs.p1.x, obs.p2.y, obs.frame.shape)
    obs = engine.reset()  # fresh unattended local-versus match, not RAM edits
```

`start()` and `close()` are idempotent. `reset()` closes the process and boots a
new match with fresh isolated NVRAM/config. No module import starts MAME.
Use the context manager or always close in `finally`. The aiohttp app owns and
closes its engine during cleanup. All engine operations use one reentrant lock;
HTTP operations run off the event loop behind an async gate, including waiting
for a cancelled request's worker before permitting another operation.

The adapter subclasses the underlying MAMEToolkit `Emulator`, reusing its Lua
frame/action loop and `step`, not its P1-vs-CPU `Environment`. The stock unbounded
console/FIFO constructor is replaced with bounded I/O and a shell-free owned
child process. Each instance has a private `TemporaryDirectory` for FIFOs,
config, font caches and NVRAM. Partial startup, frame errors and interrupts close
the child; termination escalates to kill after two seconds and always waits to
reap it. No global toolkit pipe directory is modified.

**HTTP mutation routes are disabled by default:** start/reset/stop/step are not
registered and return JSON 404 without dispatching engine operations. Read-only
health, engine status, and frame routes remain available. Both health's `engine`
and `/api/engine` report `controls_enabled` alongside the actual engine state.

Only the exact environment setting `SF3_ENABLE_LOCAL_CONTROL=1` at application
creation enables mutations. The supplied Orb portal service explicitly sets
this flag to `0`, overriding an inherited opt-in and keeping the portal read-only.
For an isolated trusted-local coordinator, launch a separate non-portal process:

```bash
SF3_ENABLE_LOCAL_CONTROL=1 PYTHONPATH=backend .venv/bin/python -m jev_fly --host 127.0.0.1 --port 8081
```

The opt-in is not authentication, authorization, or CSRF protection. Once enabled,
any reachable client can mutate the engine, including browser cross-origin simple
POSTs to lifecycle endpoints; loopback binding alone does not prevent CSRF.
Do not enable the flag on an untrusted network or public portal.

| Endpoint | Contract |
|---|---|
| `GET /api/health` | Passive prerequisites plus live `engine` status; Phase 2 |
| `GET /api/engine` | stopped/starting/running/failed, error, latest RAM/frame metadata |
| `POST /api/engine/start` | Explicit bounded startup (about 30 seconds) |
| `POST /api/engine/reset` | Full fresh-match restart |
| `POST /api/engine/stop` | Idempotent teardown |
| `POST /api/engine/step` | JSON `{"p1":["right","jab"],"p2":["up","roundhouse"]}` |
| `GET /api/engine/frame` | Latest PNG with sequence/timestamp headers; does not advance |

All POST routes require the trusted-local opt-in; otherwise they return 404.
Buttons: up/down/left/right, jab/strong/fierce, short/forward/roundhouse, start,
coin. Unknown buttons, opposing directions and malformed bodies return 400;
step bodies are limited to 4096 bytes (413 if exceeded) and 12 entries per player.
step/frame without a running/available engine returns 409; emulator failures
return 503, including malformed emulator data and missing Python dependencies.
Public errors contain only exception types, never console output or configured
ROM paths; Python callers still receive the original exceptions. Readiness does
**not** mean MAME is running or the ROM was audited.

## Frame and timing semantics

`Observation.frame` is an owned read-only NumPy `uint8` RGB array `(224,384,3)`.
It accompanies typed scalar player state, an engine-lifetime increasing
`sequence`, MAME `emulated_frame`, and `timestamp_ns` from the monotonic receipt
clock. Sequence stays increasing through resets; MAME's frame counter restarts.
Frames are read as fixed-size binary packets, not newline-delimited pixel data.

Delivery is **pull-driven**: call `step` continuously. MAME is throttled to its
native ~59.6 Hz, sampling every two emulated frames (~29.8 FPS). Actions apply
simultaneously for those two frames and then release unless repeated. Slow
consumers pause emulation at the FIFO boundary; the adapter does not drop inputs
or pretend stale frames are fresh. The latest-frame endpoint is a snapshot, not
a background game loop. The in-process `MatchCoordinator` described in
`docs/coordinator.md` owns the continuous loop for scripted Phase 3 matches.

## Proven RAM map and units

Addresses were cross-checked on 2026-09-18 against
[modal-labs/sf3 `src/env.py`](https://github.com/modal-labs/sf3/blob/main/src/env.py),
[Grouflon `gamestate.lua`](https://github.com/Grouflon/3rd_training_lua/blob/master/src/gamestate.lua),
and [Grouflon `memory_adresses.lua`](https://github.com/Grouflon/3rd_training_lua/blob/master/src/memory_adresses.lua).
`engine.MEMORY` is the implementation source of truth. No health, timer, position,
or menu RAM is written by this adapter.

| Value | P1/shared | P2 | Read type |
|---|---|---|---|
| Fighting | `02011389` | shared | u8, nonzero = fighting |
| Timer | `02011377` | shared | u8, raw game timer |
| Wins | `02011383` | `02011385` | u8 |
| Health | `02068D0A` | `020691A2` | s16, no clamping |
| Super gauge | `020695B5` | `020695E1` | u8, character/SA-specific units |
| Super stocks | `020695BF` | `020695EB` | u8 |
| Stun accumulator | `020695FD` | `02069611` | u32; public stun = value >> 24 |
| Stun timer | `020695F9` | `0206960D` | u8 |
| X | `02068CD0` | `02069168` | s16, world coordinates |
| Y | `02068CD4` | `0206916C` | s16, world coordinates |
| Select state | `0201553D` | `02015545` | u8 |
| Character ID | `02011387` | `02011388` | u8 |
| Final Super Art | `0201138B` | `0201138C` | u8, zero-based |

Modal supplies fighting/timer/health/wins/super/stun reads. Grouflon establishes
position offsets: bases `02068C6C`/`02069104`, signed X at `+64`, signed Y at
`+68`; it also establishes selection and SA addresses. Importantly, Grouflon
shifts the stun accumulator right 24 bits; returning the unshifted u32 as a
stun bar would be incorrect (the hit probe observed 167049728 rather than 9).

## Input names and unattended selection

All 24 player fields plus Service Mode are asserted against live MAME ioports
before boot. `:INPUTS` owns directions, punches, start/coin. `:EXTRA` owns P1's
three kicks and P2 short/forward. **Bundled MAME 0.211 places P2 Roundhouse Kick
on `:INPUTS`**. Its live table has no such field under `:EXTRA`; the integration
probe also demonstrates damage using `:INPUTS`. Do not infer the software port
from the physical kick harness or silently change it to match newer drivers.

Startup uses the proven Modal service-menu exit sequence: service, advance 30
frames, P1 up, 30 frames, P1 jab, 900 frames, both coins, 12 frames, both coins,
60 frames, both starts. This isolated initial sequence has no reliable menu RAM
contract and is intentionally bounded in emulated frames, not sleeps. Subsequent
selection observes both player states: 0 → start, 2 → confirm character, 4 →
confirm SA, 5 → locked. Presses are separated by released frames. Clean NVRAM
defaults to Alex/Ryu and SA I/I; startup validates those actual IDs and fighting
RAM, failing rather than silently delivering a CPU fight or wrong matchup.

Console construction has one shared 20-second budget, including nonblocking
writes. Startup checks a 90-second overall deadline between menu steps; a step
already in flight can add up to 2s for an action write and 3s for a frame read,
followed by at most 4s of termination/reaping waits. Selection is also limited to
1760 post-boot frames. Reset is deliberately a full fresh
process, not a save-state or deterministic replay guarantee. Automatic round
progression remains the game's own behavior; callers can restart at any time.

## Verification

```bash
.venv/bin/pytest -q
npm --prefix frontend test
npm --prefix frontend run build
SF3_INTEGRATION=1 .venv/bin/pytest -s -q tests/test_engine_integration.py
```

The opt-in test performs bundled `-verifyroms sfiii3n`, boots twice with a reset,
asserts actual fighting/character IDs/health/timer, frame dimensions and content,
simultaneous asymmetric movement, P2 roundhouse damage/meter/stun, 150 fresh
frames at 20–30 FPS, game-time advancement, neutral-input release for both
players, and frozen-child timeout/forced-kill cleanup. It never downloads a ROM
or writes game RAM to manufacture success.

Observed first proof: audit `1 romsets found, 1 were OK`; boot 29.95s/31.96s;
29.79 FPS both runs; P1 X 430→501 while P2 Y 0→64 with X fixed at 600;
P2 HK changed health 160/160→141/160, meter 4/22 and stun 9/0;
300 emulated frames advanced timer 94→89. Both children were reaped and both
private runtime/FIFO directories removed.

Limitations: only bundled MAME/this ROM family and fixed Alex/Ryu SA I choices
are validated; no determinism claim, mid-round save-state reset, Jev/FlyBrain
integration, authenticated remote control, or polished dashboard.

## Upstream attribution and distribution boundary

This adapter consumes the installed
[MAMEToolkit](https://github.com/M-J-Murray/MAMEToolkit) Python API and its modified
[MAME](https://github.com/M-J-Murray/mame); it does not vendor either dependency.
MAMEToolkit's [LICENSE](https://github.com/M-J-Murray/MAMEToolkit/blob/master/LICENSE)
is GPL-2.0 despite its contradictory MIT package classifier. The modified MAME
[license](https://github.com/M-J-Murray/mame/blob/master/LICENSE.md) is
GPL-2.0-or-later with per-file notices. A future distribution of dependencies or
a combined application must assess GPL obligations, preserve notices, and
provide corresponding source as required; this source-only review is not legal
clearance for binary distribution.

Modal and Grouflon are credited above for RAM facts and menu procedures. Neither
repository currently supplies a general license grant. Their source is not
vendored here: the adapter independently encodes addresses/types and functional
input sequences. Attribution alone would not authorize copying their source.
