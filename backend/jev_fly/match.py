"""Shared Phase 3 game protocol, scripted agents, and match coordinator."""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from types import MappingProxyType
from typing import Callable, Mapping, NewType, Protocol

from .engine import Button, Controls, Observation, SF3Engine
from .telemetry import MatchTelemetry


MatchId = NewType("MatchId", str)
DecisionId = NewType("DecisionId", str)
ObservationId = NewType("ObservationId", str)


class Player(str, Enum):
    P1 = "p1"
    P2 = "p2"

    @property
    def other(self) -> "Player":
        return Player.P2 if self is Player.P1 else Player.P1


class Action(str, Enum):
    NEUTRAL = "neutral"
    ADVANCE = "advance"
    RETREAT = "retreat"
    BLOCK = "block"
    JUMP = "jump"
    LIGHT_ATTACK = "light_attack"
    HEAVY_ATTACK = "heavy_attack"


ALL_ACTIONS = tuple(Action)


@dataclass(frozen=True)
class FighterSnapshot:
    health: int
    x: int
    y: int
    recent_damage: int
    previous_action: Action


@dataclass(frozen=True)
class GameSnapshot:
    """One absolute snapshot shared by both agents; player perspective is explicit."""

    observation_id: ObservationId
    sequence: int
    emulated_frame: int
    timer: int
    fighting: bool
    distance: int
    p1: FighterSnapshot
    p2: FighterSnapshot

    def fighter(self, player: Player) -> FighterSnapshot:
        return self.p1 if player is Player.P1 else self.p2

    def opponent(self, player: Player) -> FighterSnapshot:
        return self.fighter(player.other)


@dataclass(frozen=True)
class DecisionRequest:
    match_id: MatchId
    decision_id: DecisionId
    player: Player
    snapshot: GameSnapshot
    deadline_ns: int


@dataclass(frozen=True)
class Decision:
    decision_id: DecisionId
    observation_id: ObservationId
    player: Player
    agent_id: str
    scores: Mapping[Action, float]
    selected_action: Action
    started_ns: int
    completed_ns: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "scores", MappingProxyType(dict(self.scores)))


class Agent(Protocol):
    agent_id: str

    async def decide(self, request: DecisionRequest) -> Decision: ...


def normalize_observation(
    match_id: MatchId,
    current: Observation,
    previous: Observation | None,
    previous_actions: Mapping[Player, Action],
) -> GameSnapshot:
    def fighter(player: Player) -> FighterSnapshot:
        state = current.p1 if player is Player.P1 else current.p2
        prior = None if previous is None else (previous.p1 if player is Player.P1 else previous.p2)
        damage = 0 if prior is None else max(0, prior.health - state.health)
        return FighterSnapshot(state.health, state.x, state.y, damage, previous_actions[player])

    return GameSnapshot(
        observation_id=ObservationId(f"{match_id}:{current.sequence}"),
        sequence=current.sequence,
        emulated_frame=current.emulated_frame,
        timer=current.timer,
        fighting=current.fighting,
        distance=abs(current.p1.x - current.p2.x),
        p1=fighter(Player.P1),
        p2=fighter(Player.P2),
    )


def compile_action(action: Action, player: Player, snapshot: GameSnapshot) -> frozenset[Button]:
    own, opponent = snapshot.fighter(player), snapshot.opponent(player)
    if own.x == opponent.x:
        toward = Button.RIGHT if player is Player.P1 else Button.LEFT
    else:
        toward = Button.RIGHT if own.x < opponent.x else Button.LEFT
    away = Button.LEFT if toward is Button.RIGHT else Button.RIGHT
    return {
        Action.NEUTRAL: frozenset(),
        Action.ADVANCE: frozenset({toward}),
        Action.RETREAT: frozenset({away}),
        Action.BLOCK: frozenset({away}),
        Action.JUMP: frozenset({Button.UP}),
        Action.LIGHT_ATTACK: frozenset({Button.JAB}),
        Action.HEAVY_ATTACK: frozenset({Button.ROUNDHOUSE}),
    }[action]


def compile_controls(actions: Mapping[Player, Action], snapshot: GameSnapshot) -> Controls:
    return Controls(
        compile_action(actions[Player.P1], Player.P1, snapshot),
        compile_action(actions[Player.P2], Player.P2, snapshot),
    )


def _decision(request: DecisionRequest, agent_id: str, scores: dict[Action, float], started: int) -> Decision:
    selected = max(ALL_ACTIONS, key=lambda action: (scores[action], -ALL_ACTIONS.index(action)))
    return Decision(
        request.decision_id,
        request.snapshot.observation_id,
        request.player,
        agent_id,
        scores,
        selected,
        started,
        time.monotonic_ns(),
    )


class AggressiveAgent:
    agent_id = "scripted-aggressive"

    async def decide(self, request: DecisionRequest) -> Decision:
        started = time.monotonic_ns()
        distance = request.snapshot.distance
        scores = {action: 0.0 for action in ALL_ACTIONS}
        scores[Action.ADVANCE] = 8.0 if distance > 90 else 2.0
        scores[Action.LIGHT_ATTACK] = 9.0 if distance <= 75 else 1.0
        scores[Action.HEAVY_ATTACK] = 10.0 if 75 < distance <= 125 else 0.5
        scores[Action.JUMP] = 1.5
        scores[Action.BLOCK] = 2.5 if request.snapshot.fighter(request.player).recent_damage else 0.0
        scores[Action.RETREAT] = -1.0
        return _decision(request, self.agent_id, scores, started)


class DefensiveAgent:
    agent_id = "scripted-defensive"

    async def decide(self, request: DecisionRequest) -> Decision:
        started = time.monotonic_ns()
        own = request.snapshot.fighter(request.player)
        distance = request.snapshot.distance
        scores = {action: 0.0 for action in ALL_ACTIONS}
        scores[Action.BLOCK] = 10.0 if own.recent_damage or distance < 70 else 3.0
        scores[Action.RETREAT] = 8.0 if distance < 90 else 1.0
        scores[Action.LIGHT_ATTACK] = 9.0 if 70 <= distance <= 105 else 1.0
        scores[Action.HEAVY_ATTACK] = 7.0 if 85 <= distance <= 125 else 0.5
        scores[Action.ADVANCE] = 6.0 if distance > 130 else 0.0
        scores[Action.JUMP] = 1.5
        return _decision(request, self.agent_id, scores, started)


@dataclass(frozen=True)
class CoordinatorConfig:
    decision_interval_s: float = 0.5
    decision_deadline_s: float = 0.4
    rounds_to_win: int = 2
    match_timeout_s: float = 180.0

    def __post_init__(self) -> None:
        if (not math.isfinite(self.decision_interval_s)
                or not math.isfinite(self.decision_deadline_s)
                or self.decision_interval_s <= 0
                or self.decision_deadline_s <= 0
                or self.decision_deadline_s > self.decision_interval_s):
            raise ValueError("Decision timing must be positive")
        if (self.rounds_to_win <= 0 or not math.isfinite(self.match_timeout_s)
                or self.match_timeout_s <= 0):
            raise ValueError("Match bounds must be positive")


@dataclass
class _Boundary:
    snapshot: GameSnapshot
    requests: dict[Player, DecisionRequest]
    tasks: dict[Player, asyncio.Task[_CompletedDecision]]
    deadline: float


@dataclass(frozen=True)
class _CompletedDecision:
    decision: Decision
    elapsed_ms: float
    completed_at: float


class MatchCoordinator:
    """The sole owner of a continuous pull-driven SF3Engine loop."""

    def __init__(
        self,
        engine: SF3Engine,
        p1_agent: Agent,
        p2_agent: Agent,
        telemetry: MatchTelemetry,
        config: CoordinatorConfig = CoordinatorConfig(),
        clock: Callable[[], float] = time.monotonic,
    ):
        self.engine = engine
        self.agents = {Player.P1: p1_agent, Player.P2: p2_agent}
        self.telemetry = telemetry
        self.config = config
        self.clock = clock
        self.state = "stopped"
        self.match_id: MatchId | None = None
        self.latest_snapshot: GameSnapshot | None = None
        self.frames = 0

    def status(self) -> dict[str, object]:
        return {
            "state": self.state,
            "match_id": self.match_id,
            "frames": self.frames,
            "observation_id": None if self.latest_snapshot is None else self.latest_snapshot.observation_id,
            "agents": {player.value: agent.agent_id for player, agent in self.agents.items()},
        }

    def _start_boundary(self, snapshot: GameSnapshot) -> _Boundary:
        loop = asyncio.get_running_loop()
        deadline = self.clock() + self.config.decision_deadline_s
        deadline_ns = time.monotonic_ns() + int(self.config.decision_deadline_s * 1e9)
        requests, tasks = {}, {}
        for player, agent in self.agents.items():
            request = DecisionRequest(
                self.match_id,
                DecisionId(str(uuid.uuid4())),
                player,
                snapshot,
                deadline_ns,
            )
            requests[player] = request
            tasks[player] = loop.create_task(self._timed_decision(agent, request))
        return _Boundary(snapshot, requests, tasks, deadline)

    async def _timed_decision(self, agent: Agent, request: DecisionRequest) -> _CompletedDecision:
        started = self.clock()
        decision = await agent.decide(request)
        completed = self.clock()
        return _CompletedDecision(decision, max(0.0, (completed - started) * 1000), completed)

    def _valid(self, decision: object, request: DecisionRequest) -> bool:
        try:
            return (
                decision.decision_id == request.decision_id
                and decision.observation_id == request.snapshot.observation_id
                and decision.player is request.player
                and decision.agent_id == self.agents[request.player].agent_id
                and isinstance(decision.selected_action, Action)
                and set(decision.scores) == set(ALL_ACTIONS)
                and all(isinstance(action, Action) for action in decision.scores)
                and all(math.isfinite(score) for score in decision.scores.values())
            )
        except (AttributeError, TypeError, ValueError):
            return False

    async def _resolve(self, boundary: _Boundary) -> tuple[dict[Player, Action], list[dict[str, object]]]:
        actions, records = {}, []
        for player, task in boundary.tasks.items():
            request, completed = boundary.requests[player], None
            fallback, reason, decision = False, None, None
            if not task.done():
                task.cancel()
                fallback, reason = True, "timeout"
                self.telemetry.lifecycle("decision_timeout", match_id=self.match_id,
                                         decision_id=request.decision_id, player=player.value)
            else:
                try:
                    completed = task.result()
                    decision = completed.decision
                    if completed.completed_at > boundary.deadline:
                        fallback, reason = True, "timeout"
                        self.telemetry.lifecycle("decision_timeout", match_id=self.match_id,
                                                 decision_id=request.decision_id, player=player.value)
                    elif not self._valid(decision, request):
                        fallback, reason = True, "stale_or_invalid"
                        self.telemetry.lifecycle("stale_decision", match_id=self.match_id,
                                                 decision_id=request.decision_id, player=player.value)
                except (Exception, asyncio.CancelledError) as exc:
                    decision = None
                    fallback, reason = True, "agent_failure"
                    self.telemetry.lifecycle("agent_failure", match_id=self.match_id,
                                             decision_id=request.decision_id, player=player.value,
                                             error=type(exc).__name__)
            valid_decision = decision is not None and self._valid(decision, request)
            if fallback:
                action = Action.BLOCK
                scores = ({candidate.value: decision.scores[candidate] for candidate in ALL_ACTIONS}
                          if valid_decision else {candidate.value: 0.0 for candidate in ALL_ACTIONS})
                selected = decision.selected_action.value if valid_decision else None
                latency_ms = (self.config.decision_deadline_s * 1000 if completed is None
                              else completed.elapsed_ms)
            else:
                action = decision.selected_action
                scores = {candidate.value: decision.scores[candidate] for candidate in ALL_ACTIONS}
                selected = action.value
                latency_ms = completed.elapsed_ms
            actions[player] = action
            records.append({
                "type": "decision",
                "match_id": self.match_id,
                "decision_id": request.decision_id,
                "observation_id": request.snapshot.observation_id,
                "player": player.value,
                "agent_id": self.agents[player].agent_id,
                "sequence": request.snapshot.sequence,
                "emulated_frame": request.snapshot.emulated_frame,
                "snapshot": snapshot_dict(request.snapshot),
                "scores": scores,
                "selected_action": selected,
                "applied_action": action.value,
                "fallback": fallback,
                "deadline_status": reason or "met",
                "latency_ms": latency_ms,
            })
        done, pending = await asyncio.wait(
            boundary.tasks.values(), timeout=self.config.decision_deadline_s
        )
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        if pending:
            self.telemetry.lifecycle("agent_cleanup_timeout", match_id=self.match_id,
                                     pending_tasks=len(pending))
            for task in pending:
                task.add_done_callback(_consume_task_result)
            raise RuntimeError("Agent task did not stop after cancellation")
        return actions, records

    async def run(self, *, match_id: MatchId | None = None, max_observations: int | None = None) -> dict[str, object]:
        if self.state != "stopped":
            raise RuntimeError("Coordinator is already active")
        self.match_id = match_id or MatchId(str(uuid.uuid4()))
        self.state, self.frames, self.latest_snapshot = "starting", 0, None
        prior: Observation | None = None
        previous_actions = {Player.P1: Action.NEUTRAL, Player.P2: Action.NEUTRAL}
        boundary: _Boundary | None = None
        pending_records: list[dict[str, object]] = []
        controls = Controls()
        next_boundary = self.clock()
        match_deadline = next_boundary + self.config.match_timeout_s
        outcome = "stopped"
        self.telemetry.start(self.match_id)
        self.telemetry.lifecycle("match_start", match_id=self.match_id)
        try:
            current = await asyncio.to_thread(self.engine.start)
            self.telemetry.lifecycle("emulator_start", match_id=self.match_id,
                                     observation_id=f"{self.match_id}:{current.sequence}")
            self.state = "running"
            snapshot = normalize_observation(self.match_id, current, prior, previous_actions)
            self.latest_snapshot = snapshot
            round_active = current.fighting
            if current.fighting:
                self.telemetry.lifecycle("round_start", match_id=self.match_id,
                                         observation_id=snapshot.observation_id)
            while True:
                now = self.clock()
                if boundary is None and now >= next_boundary:
                    boundary = self._start_boundary(snapshot)
                    while next_boundary <= now:
                        next_boundary += self.config.decision_interval_s
                if boundary is not None and (
                    all(task.done() for task in boundary.tasks.values()) or now >= boundary.deadline
                ):
                    previous_actions, pending_records = await self._resolve(boundary)
                    boundary = None

                # Actions are semantic and held until the next decision, but are
                # recompiled from current positions for every exact two-frame step.
                controls = compile_controls(previous_actions, snapshot)
                control_snapshot = snapshot
                prior = current
                current = await asyncio.to_thread(self.engine.step, controls)
                self.frames += 1
                snapshot = normalize_observation(self.match_id, current, prior, previous_actions)
                self.latest_snapshot = snapshot
                if pending_records:
                    controls_payload = controls_dict(controls)
                    result = result_dict(snapshot)
                    for record in pending_records:
                        self.telemetry.emit({**record, "control_snapshot": snapshot_dict(control_snapshot),
                                             "actual_controls": controls_payload, "result": result})
                    pending_records = []
                wins_changed = (prior.p1.wins, prior.p2.wins) != (current.p1.wins, current.p2.wins)
                if round_active and (wins_changed or (prior.fighting and not current.fighting)):
                    self.telemetry.lifecycle("round_end", match_id=self.match_id,
                                             observation_id=snapshot.observation_id,
                                             wins={"p1": current.p1.wins, "p2": current.p2.wins},
                                             transition="wins" if wins_changed else "fighting")
                    round_active = False
                if wins_changed and max(current.p1.wins, current.p2.wins) >= self.config.rounds_to_win:
                    outcome = Player.P1.value if current.p1.wins > current.p2.wins else Player.P2.value
                    self.telemetry.lifecycle("match_complete", match_id=self.match_id, winner=outcome)
                    break
                if current.fighting and not round_active and (
                    not prior.fighting or current.timer > prior.timer
                ):
                    self.telemetry.lifecycle("round_start", match_id=self.match_id,
                                             observation_id=snapshot.observation_id,
                                             wins={"p1": current.p1.wins, "p2": current.p2.wins})
                    round_active = True
                if self.clock() >= match_deadline:
                    outcome = "timeout"
                    self.telemetry.lifecycle("match_timeout", match_id=self.match_id,
                                             observations=self.frames)
                    break
                if max_observations is not None and self.frames >= max_observations:
                    outcome = "observation_limit"
                    break
        except asyncio.CancelledError:
            outcome = "cancelled"
            self.telemetry.lifecycle("match_cancelled", match_id=self.match_id)
            raise
        except Exception as exc:
            outcome = "failed"
            self.telemetry.lifecycle("process_failure", match_id=self.match_id, error=type(exc).__name__)
            raise
        finally:
            self.state = "stopping"
            try:
                if boundary is not None:
                    for task in boundary.tasks.values():
                        task.cancel()
                    done, pending = await asyncio.wait(
                        boundary.tasks.values(), timeout=self.config.decision_deadline_s
                    )
                    if done:
                        await asyncio.gather(*done, return_exceptions=True)
                    if pending:
                        self.telemetry.lifecycle("agent_cleanup_timeout", match_id=self.match_id,
                                                 pending_tasks=len(pending))
                        for task in pending:
                            task.add_done_callback(_consume_task_result)
            finally:
                close_task = asyncio.create_task(asyncio.to_thread(self.engine.close))
                cancelled_during_close = False
                while not close_task.done():
                    try:
                        await asyncio.shield(close_task)
                    except asyncio.CancelledError:
                        cancelled_during_close = True
                try:
                    close_task.result()
                finally:
                    self.telemetry.lifecycle("cleanup", match_id=self.match_id)
                    self.state = "stopped"
                    self.telemetry.finish({"match_id": self.match_id, "outcome": outcome,
                                           "observations": self.frames})
                if cancelled_during_close:
                    raise asyncio.CancelledError
        return {"match_id": self.match_id, "outcome": outcome, "observations": self.frames}


def snapshot_dict(snapshot: GameSnapshot) -> dict[str, object]:
    result = asdict(snapshot)
    result["observation_id"] = str(snapshot.observation_id)
    result["p1"]["previous_action"] = snapshot.p1.previous_action.value
    result["p2"]["previous_action"] = snapshot.p2.previous_action.value
    return result


def controls_dict(controls: Controls) -> dict[str, list[str]]:
    return {"p1": sorted(button.value for button in controls.p1),
            "p2": sorted(button.value for button in controls.p2)}


def result_dict(snapshot: GameSnapshot) -> dict[str, object]:
    return {
        "observation_id": snapshot.observation_id,
        "sequence": snapshot.sequence,
        "emulated_frame": snapshot.emulated_frame,
        "p1": {"health": snapshot.p1.health, "x": snapshot.p1.x, "y": snapshot.p1.y,
               "damage": snapshot.p1.recent_damage},
        "p2": {"health": snapshot.p2.health, "x": snapshot.p2.x, "y": snapshot.p2.y,
               "damage": snapshot.p2.recent_damage},
    }


def _consume_task_result(task: asyncio.Task[object]) -> None:
    if not task.cancelled():
        task.exception()
