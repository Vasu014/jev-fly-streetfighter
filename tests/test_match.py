import asyncio
import json
import time

import numpy as np
import pytest

from jev_fly.engine import Button, Controls, Observation, PlayerState
from jev_fly.match import (
    ALL_ACTIONS,
    Action,
    CoordinatorConfig,
    Decision,
    DecisionId,
    FighterSnapshot,
    GameSnapshot,
    MatchCoordinator,
    MatchId,
    ObservationId,
    Player,
    normalize_observation,
    compile_action,
)
from jev_fly.telemetry import MatchTelemetry


def snapshot(x1=100, x2=200):
    fighter = lambda x: FighterSnapshot(160, x, 0, 0, Action.NEUTRAL)
    return GameSnapshot(ObservationId("m:1"), 1, 2, 99, True, abs(x1 - x2),
                        fighter(x1), fighter(x2))


def test_player_relative_macros_are_asymmetric_and_reverse_after_side_switch():
    before = snapshot(100, 200)
    crossed = snapshot(250, 150)
    assert compile_action(Action.ADVANCE, Player.P1, before) == {Button.RIGHT}
    assert compile_action(Action.ADVANCE, Player.P2, before) == {Button.LEFT}
    assert compile_action(Action.RETREAT, Player.P1, before) == {Button.LEFT}
    assert compile_action(Action.BLOCK, Player.P2, before) == {Button.RIGHT}
    assert compile_action(Action.ADVANCE, Player.P1, crossed) == {Button.LEFT}
    assert compile_action(Action.ADVANCE, Player.P2, crossed) == {Button.RIGHT}
    assert compile_action(Action.BLOCK, Player.P1, crossed) == {Button.RIGHT}
    assert compile_action(Action.LIGHT_ATTACK, Player.P2, crossed) == {Button.JAB}
    assert compile_action(Action.HEAVY_ATTACK, Player.P1, crossed) == {Button.ROUNDHOUSE}
    tied = snapshot(180, 180)
    assert compile_action(Action.ADVANCE, Player.P1, tied) == {Button.RIGHT}
    assert compile_action(Action.ADVANCE, Player.P2, tied) == {Button.LEFT}


def test_normalization_is_shared_absolute_state_with_damage_and_previous_actions():
    previous = observation(8, x1=-40, x2=310, health1=150, health2=120)
    current = observation(9, x1=-15, x2=280, health1=143, health2=125)
    normalized = normalize_observation(
        MatchId("normalize"), current, previous,
        {Player.P1: Action.ADVANCE, Player.P2: Action.BLOCK},
    )
    assert normalized.observation_id == "normalize:9"
    assert normalized.distance == 295
    assert (normalized.p1.health, normalized.p1.x, normalized.p1.recent_damage,
            normalized.p1.previous_action) == (143, -15, 7, Action.ADVANCE)
    # Healing is not damage, and P2's explicit perspective/action is retained.
    assert (normalized.p2.health, normalized.p2.x, normalized.p2.recent_damage,
            normalized.p2.previous_action) == (125, 280, 0, Action.BLOCK)
    assert set(normalized.__dataclass_fields__) == {
        "observation_id", "sequence", "emulated_frame", "timer", "fighting",
        "distance", "p1", "p2",
    }


def observation(sequence, *, x1=100, x2=200, health1=160, health2=160, wins1=0, wins2=0):
    def player(health, wins, x):
        return PlayerState(health, wins, 0, 0, 0, 0, x, 0, 1, 0)
    frame = np.zeros((224, 384, 3), dtype=np.uint8)
    frame.flags.writeable = False
    return Observation(sequence, sequence * 2, time.monotonic_ns(), True, 99,
                       player(health1, wins1, x1), player(health2, wins2, x2), frame)


class FakeEngine:
    def __init__(self, delay=0):
        self.sequence = 1
        self.controls = []
        self.closed = 0
        self.delay = delay

    def start(self):
        return observation(self.sequence)

    def step(self, controls):
        if self.delay:
            time.sleep(self.delay)
        self.controls.append(controls)
        self.sequence += 1
        return observation(self.sequence)

    def close(self):
        self.closed += 1


class ScheduledClockEngine(FakeEngine):
    def __init__(self, clock, advances):
        super().__init__()
        self.clock = clock
        self.advances = iter(advances)

    def step(self, controls):
        self.controls.append(controls)
        self.clock[0] += next(self.advances)
        self.sequence += 1
        return observation(self.sequence)


class CrossingEngine(FakeEngine):
    def step(self, controls):
        self.controls.append(controls)
        self.sequence += 1
        return observation(self.sequence, x1=250, x2=150)


class FixedAgent:
    def __init__(self, agent_id, action):
        self.agent_id = agent_id
        self.action = action
        self.requests = []

    async def decide(self, request):
        self.requests.append(request)
        now = time.monotonic_ns()
        scores = {action: float(action is self.action) for action in ALL_ACTIONS}
        return Decision(request.decision_id, request.snapshot.observation_id, request.player,
                        self.agent_id, scores, self.action, now, time.monotonic_ns())


@pytest.mark.asyncio
async def test_coordinator_shares_boundary_and_correlates_inputs_with_result(tmp_path):
    engine = FakeEngine()
    p1, p2 = FixedAgent("aggressive", Action.ADVANCE), FixedAgent("defensive", Action.ADVANCE)
    telemetry = MatchTelemetry(tmp_path, capacity=20)
    coordinator = MatchCoordinator(
        engine, p1, p2, telemetry,
        CoordinatorConfig(decision_interval_s=60, decision_deadline_s=1),
    )
    result = await coordinator.run(match_id=MatchId("shared"), max_observations=3)
    assert result == {"match_id": "shared", "outcome": "observation_limit", "observations": 3}
    assert p1.requests[0].snapshot is p2.requests[0].snapshot
    assert p1.requests[0].snapshot.observation_id == p2.requests[0].snapshot.observation_id
    assert any(control == Controls({Button.RIGHT}, {Button.LEFT}) for control in engine.controls)
    decisions = [event for event in telemetry.events if event["type"] == "decision"]
    assert len(decisions) == 2
    assert {event["player"] for event in decisions} == {"p1", "p2"}
    for event in decisions:
        assert set(event["scores"]) == {action.value for action in ALL_ACTIONS}
        assert event["match_id"] == "shared"
        assert event["observation_id"] == event["snapshot"]["observation_id"]
        assert event["control_snapshot"]["sequence"] + 1 == event["result"]["sequence"]
        assert event["result"]["observation_id"] != event["observation_id"]
        assert event["actual_controls"] == {"p1": ["right"], "p2": ["left"]}
    lines = (tmp_path / "shared" / "events.jsonl").read_text().splitlines()
    assert len(lines) == len(telemetry.events)
    assert json.loads((tmp_path / "shared" / "summary.json").read_text())["observations"] == 3
    assert engine.closed == 1 and coordinator.state == "stopped"


@pytest.mark.asyncio
async def test_decision_boundaries_remain_anchored_after_a_late_sample(tmp_path):
    clock = [0.0]
    engine = ScheduledClockEngine(clock, [0.62, 0.01, 0.31, 0.06, 0.01])
    p1, p2 = FixedAgent("p1", Action.NEUTRAL), FixedAgent("p2", Action.NEUTRAL)
    coordinator = MatchCoordinator(
        engine, p1, p2, MatchTelemetry(tmp_path),
        CoordinatorConfig(decision_interval_s=0.5, decision_deadline_s=0.4),
        clock=lambda: clock[0],
    )
    await coordinator.run(match_id=MatchId("drift"), max_observations=5)
    # The 0.5 boundary is observed late at 0.63. Anchoring the next boundary to
    # 1.0 catches it; scheduling from 0.63 would drift to 1.13 and miss it.
    assert len(p1.requests) == len(p2.requests) == 3
    assert [request.snapshot.sequence for request in p1.requests] == [1, 3, 5]


def test_coordinator_rejects_unbounded_timing_configuration():
    with pytest.raises(ValueError, match="Decision timing"):
        CoordinatorConfig(decision_interval_s=0)
    with pytest.raises(ValueError, match="Decision timing"):
        CoordinatorConfig(decision_interval_s=0.5, decision_deadline_s=0.6)
    with pytest.raises(ValueError, match="Match bounds"):
        CoordinatorConfig(match_timeout_s=float("nan"))


@pytest.mark.asyncio
async def test_held_semantic_actions_recompile_after_crossing(tmp_path):
    engine = CrossingEngine()
    telemetry = MatchTelemetry(tmp_path)
    coordinator = MatchCoordinator(
        engine,
        FixedAgent("p1", Action.ADVANCE),
        FixedAgent("p2", Action.ADVANCE),
        telemetry,
        CoordinatorConfig(decision_interval_s=60, decision_deadline_s=1),
    )
    await coordinator.run(match_id=MatchId("crossing"), max_observations=3)
    # The decision used the initial 100/200 snapshot, but by its first applied
    # step positions had crossed. Compiling only at the boundary gets this wrong.
    assert engine.controls[-1] == Controls({Button.LEFT}, {Button.RIGHT})
    decisions = [event for event in telemetry.events if event["type"] == "decision"]
    assert all(event["actual_controls"] == {"p1": ["left"], "p2": ["right"]}
               for event in decisions)
    assert all(event["control_snapshot"]["p1"]["x"] == 250 and
               event["control_snapshot"]["p2"]["x"] == 150 for event in decisions)


class HangingAgent:
    agent_id = "hanging"

    def __init__(self):
        self.cancelled = False

    async def decide(self, _request):
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


class SlowCancellingAgent:
    agent_id = "slow-cancelling"

    def __init__(self):
        self.cancelled = 0

    async def decide(self, _request):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            await asyncio.sleep(10)


class StaleAgent(FixedAgent):
    async def decide(self, request):
        decision = await super().decide(request)
        return Decision(DecisionId("previous-boundary"), decision.observation_id, decision.player,
                        decision.agent_id, decision.scores, decision.selected_action,
                        decision.started_ns, decision.completed_ns)


class LateAgent(FixedAgent):
    async def decide(self, request):
        await asyncio.sleep(0.005)
        decision = await super().decide(request)
        # Agent-provided timestamps are telemetry, not trusted deadline evidence.
        return Decision(decision.decision_id, decision.observation_id, decision.player,
                        decision.agent_id, decision.scores, decision.selected_action, 0, 1)


class MalformedAgent:
    agent_id = "malformed"

    async def decide(self, _request):
        return object()


@pytest.mark.asyncio
async def test_deadline_and_stale_results_fall_back_and_cancel(tmp_path):
    engine = FakeEngine(delay=0.005)
    hanging = HangingAgent()
    stale = StaleAgent("stale", Action.HEAVY_ATTACK)
    telemetry = MatchTelemetry(tmp_path)
    coordinator = MatchCoordinator(
        engine, hanging, stale, telemetry,
        CoordinatorConfig(decision_interval_s=60, decision_deadline_s=0.001),
    )
    await coordinator.run(match_id=MatchId("fallback"), max_observations=3)
    decisions = [event for event in telemetry.events if event["type"] == "decision"]
    assert {(event["player"], event["deadline_status"], event["applied_action"])
            for event in decisions} == {
                ("p1", "timeout", "block"), ("p2", "stale_or_invalid", "block")}
    assert any(control == Controls({Button.LEFT}, {Button.RIGHT}) for control in engine.controls)
    assert hanging.cancelled
    lifecycle = [event["event"] for event in telemetry.events if event["type"] == "lifecycle"]
    assert "decision_timeout" in lifecycle and "stale_decision" in lifecycle


@pytest.mark.asyncio
async def test_late_agent_cannot_forge_timing_and_preserves_returned_scores(tmp_path):
    engine = FakeEngine(delay=0.01)
    telemetry = MatchTelemetry(tmp_path)
    coordinator = MatchCoordinator(
        engine,
        LateAgent("late", Action.HEAVY_ATTACK),
        FixedAgent("on-time", Action.NEUTRAL),
        telemetry,
        CoordinatorConfig(decision_interval_s=60, decision_deadline_s=0.001),
    )
    await coordinator.run(match_id=MatchId("late"), max_observations=2)
    late = next(event for event in telemetry.events
                if event.get("type") == "decision" and event["player"] == "p1")
    assert late["deadline_status"] == "timeout"
    assert late["selected_action"] == "heavy_attack"
    assert late["applied_action"] == "block" and late["fallback"] is True
    assert late["scores"]["heavy_attack"] == 1.0
    assert engine.controls[-1].p1 == {Button.LEFT}


@pytest.mark.asyncio
async def test_malformed_agent_result_uses_safe_fallback(tmp_path):
    engine = FakeEngine()
    telemetry = MatchTelemetry(tmp_path)
    coordinator = MatchCoordinator(
        engine,
        MalformedAgent(),
        FixedAgent("valid", Action.NEUTRAL),
        telemetry,
        CoordinatorConfig(decision_interval_s=60, decision_deadline_s=1),
    )
    result = await coordinator.run(match_id=MatchId("malformed"), max_observations=2)
    malformed = next(event for event in telemetry.events
                     if event.get("type") == "decision" and event["player"] == "p1")
    assert result["outcome"] == "observation_limit"
    assert malformed["deadline_status"] == "stale_or_invalid"
    assert malformed["selected_action"] is None and malformed["applied_action"] == "block"
    assert engine.closed == 1


@pytest.mark.asyncio
async def test_external_cancellation_cleans_agent_and_engine(tmp_path):
    engine = FakeEngine(delay=0.01)
    agents = HangingAgent(), HangingAgent()
    telemetry = MatchTelemetry(tmp_path)
    coordinator = MatchCoordinator(
        engine, *agents, telemetry,
        CoordinatorConfig(decision_interval_s=60, decision_deadline_s=30),
    )
    run = asyncio.create_task(coordinator.run(match_id=MatchId("cancel")))
    for _ in range(100):
        if coordinator.state == "running":
            break
        await asyncio.sleep(0.001)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert engine.closed == 1 and coordinator.state == "stopped"
    assert all(agent.cancelled for agent in agents)
    assert "match_cancelled" in [event.get("event") for event in telemetry.events]
    assert "cleanup" in [event.get("event") for event in telemetry.events]


@pytest.mark.asyncio
async def test_agent_ignoring_cancellation_aborts_match_and_closes_engine(tmp_path):
    engine = FakeEngine(delay=0.005)
    slow = SlowCancellingAgent()
    telemetry = MatchTelemetry(tmp_path)
    coordinator = MatchCoordinator(
        engine,
        slow,
        FixedAgent("valid", Action.NEUTRAL),
        telemetry,
        CoordinatorConfig(decision_interval_s=1, decision_deadline_s=0.001),
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="did not stop"):
        await coordinator.run(match_id=MatchId("slow-cancel"))
    assert time.monotonic() - started < 0.1
    assert slow.cancelled == 1
    assert engine.closed == 1 and coordinator.state == "stopped"
    lifecycle = [event.get("event") for event in telemetry.events]
    assert "agent_cleanup_timeout" in lifecycle
    assert "process_failure" in lifecycle and "cleanup" in lifecycle


@pytest.mark.asyncio
async def test_match_timeout_is_bounded_and_cleans_up(tmp_path):
    engine = FakeEngine(delay=0.005)
    telemetry = MatchTelemetry(tmp_path)
    coordinator = MatchCoordinator(
        engine,
        FixedAgent("p1", Action.NEUTRAL),
        FixedAgent("p2", Action.NEUTRAL),
        telemetry,
        CoordinatorConfig(decision_interval_s=60, decision_deadline_s=1,
                          match_timeout_s=0.001),
    )
    result = await coordinator.run(match_id=MatchId("bounded"))
    assert result["outcome"] == "timeout" and result["observations"] == 1
    assert engine.closed == 1
    assert "match_timeout" in [event.get("event") for event in telemetry.events]


def test_telemetry_write_failure_keeps_bounded_memory_and_summary_attempt(tmp_path):
    calls = []

    def fail_open(path, mode, **_kwargs):
        calls.append((path.name, mode))
        raise OSError("private filesystem detail")

    telemetry = MatchTelemetry(tmp_path, capacity=2, open_file=fail_open)
    telemetry.start("write-failure")
    for number in range(3):
        telemetry.emit({"type": "test", "number": number})
    telemetry.finish({"outcome": "safe"})
    assert telemetry.events[-1]["event"] == "telemetry_write_failure"
    assert telemetry.events[-1]["operation"] == "summary"
    assert telemetry.write_failures == 4
    assert calls[-1] == ("summary.json", "w")


def test_existing_run_directory_disables_persistence_instead_of_appending(tmp_path):
    run = tmp_path / "duplicate"
    run.mkdir()
    telemetry = MatchTelemetry(tmp_path)
    telemetry.start("duplicate")
    telemetry.emit({"type": "test"})
    telemetry.finish({"outcome": "safe"})
    assert telemetry.directory is None
    assert telemetry.write_failures == 1
    assert not (run / "events.jsonl").exists()
    assert not (run / "summary.json").exists()


def test_match_id_cannot_escape_telemetry_root(tmp_path):
    root = tmp_path / "runs"
    telemetry = MatchTelemetry(root)
    telemetry.start("../escaped")
    telemetry.emit({"type": "test"})
    assert telemetry.directory is None
    assert telemetry.write_failures == 1
    assert not (tmp_path / "escaped").exists()
