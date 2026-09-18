"""Serialized, pull-driven local-versus SFIII engine. Importing never starts MAME."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np


class Button(str, Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    JAB = "jab"
    STRONG = "strong"
    FIERCE = "fierce"
    SHORT = "short"
    FORWARD = "forward"
    ROUNDHOUSE = "roundhouse"
    START = "start"
    COIN = "coin"


@dataclass(frozen=True)
class Controls:
    p1: frozenset[Button] = frozenset()
    p2: frozenset[Button] = frozenset()

    def __post_init__(self):
        for name in ("p1", "p2"):
            values = frozenset(Button(value) for value in getattr(self, name))
            if {Button.LEFT, Button.RIGHT} <= values or {Button.UP, Button.DOWN} <= values:
                raise ValueError("Opposing directions are not supported")
            object.__setattr__(self, name, values)


def input_fields(controls: Controls) -> list[tuple[str, str]]:
    fields = []
    names = {"jab": "Jab Punch", "strong": "Strong Punch", "fierce": "Fierce Punch",
             "short": "Short Kick", "forward": "Forward Kick", "roundhouse": "Roundhouse Kick"}
    for player, buttons in enumerate((controls.p1, controls.p2), 1):
        for button in sorted(buttons):
            if button == Button.COIN:
                field = f"Coin {player}"
            elif button == Button.START:
                field = "1 Player Start" if player == 1 else "2 Players Start"
            else:
                field = f"P{player} {names.get(button, button.value.title())}"
            port = ":EXTRA" if button in (Button.SHORT, Button.FORWARD, Button.ROUNDHOUSE) else ":INPUTS"
            # Verified against the bundled 0.211 live ioport table, not inferred
            # from the physical kick harness: P2 HK is on INPUTS in this build.
            if player == 2 and button == Button.ROUNDHOUSE:
                port = ":INPUTS"
            fields.append((port, field))
    return fields


# Provenance and raw units are documented in docs/emulator.md.
MEMORY = {
    "fighting": (0x02011389, "u8"), "timer": (0x02011377, "u8"),
    "wins1": (0x02011383, "u8"), "wins2": (0x02011385, "u8"),
    "health1": (0x02068D0A, "s16"), "health2": (0x020691A2, "s16"),
    "meter1": (0x020695B5, "u8"), "meter2": (0x020695E1, "u8"),
    "stocks1": (0x020695BF, "u8"), "stocks2": (0x020695EB, "u8"),
    "stun1": (0x020695FD, "u32"), "stun2": (0x02069611, "u32"),
    "stun_timer1": (0x020695F9, "u8"), "stun_timer2": (0x0206960D, "u8"),
    "x1": (0x02068CD0, "s16"), "x2": (0x02069168, "s16"),
    "y1": (0x02068CD4, "s16"), "y2": (0x0206916C, "s16"),
    "select1": (0x0201553D, "u8"), "select2": (0x02015545, "u8"),
    "character1": (0x02011387, "u8"), "character2": (0x02011388, "u8"),
    "super_art1": (0x0201138B, "u8"), "super_art2": (0x0201138C, "u8"),
}


@dataclass(frozen=True)
class PlayerState:
    health: int
    wins: int
    meter: int
    stocks: int
    stun: int
    stun_timer: int
    x: int
    y: int
    character: int
    super_art: int


@dataclass(frozen=True)
class Observation:
    sequence: int
    emulated_frame: int
    timestamp_ns: int  # monotonic capture receipt time, not wall-clock time
    fighting: bool
    timer: int
    p1: PlayerState
    p2: PlayerState
    frame: np.ndarray  # owned, read-only uint8 RGB (224, 384, 3)


def observation(raw: dict, sequence: int) -> Observation:
    frame = np.array(raw["frame"], dtype=np.uint8, copy=True)
    if frame.shape != (224, 384, 3):
        raise ValueError(f"Unexpected MAME RGB frame shape: {frame.shape}")
    frame.flags.writeable = False
    players = []
    for p in (1, 2):
        values = {name: int(raw[f"{name}{p}"]) for name in PlayerState.__dataclass_fields__}
        # Grouflon reads the high byte of this u32 fixed-point stun accumulator.
        values["stun"] >>= 24
        players.append(PlayerState(**values))
    return Observation(sequence, int(raw["emulated_frame"]), time.monotonic_ns(),
                       bool(raw["fighting"]), int(raw["timer"]), *players, frame)


class SF3Engine:
    """Call step continuously at ~30 Hz; actions last exactly one 2-frame step.

    MAME throttles to native speed. A slow consumer intentionally pauses the game
    at the FIFO boundary, rather than silently dropping control or observations.
    All lifecycle/control operations are serialized; reset starts a fresh match.
    """

    def __init__(self, rom: Path):
        self.rom = Path(rom).resolve()
        self._lock = threading.RLock()
        self._emu = None
        self._sequence = 0
        self.state = "stopped"
        self.error: str | None = None
        self.latest: Observation | None = None

    def start(self) -> Observation:
        with self._lock:
            if self._emu is not None:
                return self.latest
            self.state, self.error = "starting", None
            self._startup_deadline = time.monotonic() + 90
            try:
                if self.rom.name != "sfiii3n.zip" or not self.rom.is_file():
                    raise FileNotFoundError("Expected a supplied sfiii3n.zip ROM")
                from ._mame import ManagedEmulator
                self._emu = ManagedEmulator(self.rom)
                self._boot()
                self.state = "running"
                return self.latest
            except BaseException as exc:
                self._fail(exc)
                raise

    def _fail(self, exc: BaseException):
        self.close()
        # Exceptions still reach Python callers; HTTP status must not expose
        # console output or filesystem paths embedded in their messages.
        self.state, self.error = "failed", type(exc).__name__

    def _step(self, controls=Controls(), *, service=False):
        if self.state == "starting" and time.monotonic() >= self._startup_deadline:
            raise TimeoutError("Local-versus startup exceeded 90 seconds")
        raw = self._emu.sample(input_fields(controls), service=service)
        self._sequence += 1
        self.latest = observation(raw, self._sequence)
        return raw

    def _boot(self):
        # Known service-menu exit sequence. Frame counts, never wall-clock sleeps.
        self._step(service=True)
        for wait, buttons in [(30, Controls({Button.UP})), (30, Controls({Button.JAB})),
                              (900, Controls({Button.COIN}, {Button.COIN})),
                              (12, Controls({Button.COIN}, {Button.COIN})),
                              (60, Controls({Button.START}, {Button.START}))]:
            for _ in range(wait // 2):
                self._step()
            self._step(buttons)
        # Clean NVRAM cursors are Alex/Ryu; no movement chooses SA I for both.
        # Release between presses: toolkit actions hold for one sampled step.
        for tick in range(800):
            raw = self._step()
            if raw["fighting"] and raw["select1"] == raw["select2"] == 5:
                if (raw["character1"], raw["character2"], raw["super_art1"], raw["super_art2"]) != (1, 2, 0, 0):
                    raise RuntimeError(f"Unexpected local-versus selection: {self.latest.p1}, {self.latest.p2}")
                return
            if tick % 10 == 0:
                buttons = []
                for p in (1, 2):
                    state = raw[f"select{p}"]
                    buttons.append({Button.START} if state == 0 else
                                   {Button.JAB} if state in (2, 4) else set())
                self._step(Controls(*buttons))
        raise TimeoutError("Local-versus startup exceeded 1760 menu frames")

    def step(self, controls: Controls = Controls()) -> Observation:
        with self._lock:
            if not isinstance(controls, Controls):
                raise TypeError("Expected Controls")
            if self.state != "running":
                raise RuntimeError("SF3Engine is not running")
            try:
                self._step(controls)
                return self.latest
            except BaseException as exc:
                self._fail(exc)
                raise

    def reset(self) -> Observation:
        with self._lock:
            self.close()
            return self.start()

    def close(self):
        with self._lock:
            emu, self._emu = self._emu, None
            try:
                if emu is not None:
                    emu.close()
            finally:
                self.state = "stopped"
                self.error = None
                self.latest = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()
