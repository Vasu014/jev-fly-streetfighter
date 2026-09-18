import os
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from jev_fly.engine import Button as B, Controls, MEMORY, SF3Engine, input_fields, observation


def raw_state():
    raw = {name: 0 for name in MEMORY}
    raw.update(fighting=1, timer=73, health1=-1, health2=149, wins1=2, wins2=1,
               meter1=31, meter2=67, stocks1=1, stocks2=3,
               stun1=0x0AFFFFFF, stun2=0x03000001, stun_timer2=7,
               x1=-12, y1=40, x2=601, y2=-3,
               character1=1, character2=2, super_art2=2, emulated_frame=1234,
               frame=np.full((224, 384, 3), (10, 43, 200), dtype=np.uint8))
    return raw


def test_asymmetric_inputs_and_all_six_attacks():
    assert input_fields(Controls({B.RIGHT, B.JAB, B.ROUNDHOUSE}, {B.UP, B.ROUNDHOUSE})) == [
        (":INPUTS", "P1 Jab Punch"), (":INPUTS", "P1 Right"),
        (":EXTRA", "P1 Roundhouse Kick"), (":INPUTS", "P2 Roundhouse Kick"),
        (":INPUTS", "P2 Up")]
    attacks = {B.JAB, B.STRONG, B.FIERCE, B.SHORT, B.FORWARD, B.ROUNDHOUSE}
    fields = input_fields(Controls(attacks, attacks))
    assert len(set(fields)) == 12
    assert input_fields(Controls({B.COIN}, {B.START})) == [
        (":INPUTS", "Coin 1"), (":INPUTS", "2 Players Start")]
    with pytest.raises(ValueError):
        Controls({B.LEFT, B.RIGHT})
    with pytest.raises(ValueError):
        Controls(p2={"not-a-button"})


def test_state_signed_positions_health_stun_fixed_point_and_owned_rgb():
    raw = raw_state()
    obs = observation(raw, 9)
    assert (obs.p1.health, obs.p2.health, obs.p1.wins, obs.p2.wins) == (-1, 149, 2, 1)
    assert (obs.p1.x, obs.p1.y, obs.p2.x, obs.p2.y) == (-12, 40, 601, -3)
    assert (obs.p1.stun, obs.p2.stun, obs.p2.stun_timer) == (10, 3, 7)
    assert (obs.p1.meter, obs.p2.meter, obs.p1.stocks, obs.p2.stocks) == (31, 67, 1, 3)
    assert (obs.sequence, obs.emulated_frame, obs.timer, obs.fighting) == (9, 1234, 73, True)
    raw["frame"][:] = 0
    assert obs.frame[0, 0].tolist() == [10, 43, 200]
    assert not obs.frame.flags.writeable
    raw["frame"] = np.zeros((2, 3, 4))
    with pytest.raises(ValueError, match="shape"):
        observation(raw, 10)


class FakeEmulator:
    instances = []

    def __init__(self, _):
        self.closed = 0
        self.calls = []
        self.fail = False
        self.instances.append(self)

    def sample(self, fields, **kwargs):
        self.calls.append((fields, kwargs))
        if self.fail:
            raise TimeoutError("frame stalled")
        return raw_state()

    def close(self):
        self.closed += 1


@pytest.fixture
def fake_engine(monkeypatch, tmp_path):
    from jev_fly import _mame
    monkeypatch.setattr(_mame, "ManagedEmulator", FakeEmulator)
    rom = tmp_path / "sfiii3n.zip"
    rom.touch()
    return SF3Engine(rom)


def test_lifecycle_idempotence_failure_and_reset(fake_engine, monkeypatch):
    e = fake_engine
    monkeypatch.setattr(e, "_boot", lambda: e._step())
    first = e.start()
    emu = e._emu
    assert e.start() is first
    with pytest.raises(TypeError, match="Expected Controls"):
        e.step({"p1": ["right"]})
    assert e.state == "running" and e.latest is first and emu.closed == 0
    e.step(Controls({B.RIGHT}, {B.UP}))
    assert emu.calls[-1][0] == [(":INPUTS", "P1 Right"), (":INPUTS", "P2 Up")]
    emu.fail = True
    with pytest.raises(TimeoutError):
        e.step()
    assert e.state == "failed" and e.latest is None and e.error == "TimeoutError"
    assert emu.closed == 1
    fresh = e.reset()
    assert fresh.sequence > first.sequence and e.error is None
    emu = e._emu
    e.close()
    e.close()
    assert emu.closed == 1
    with pytest.raises(RuntimeError, match="not running"):
        e.step()


def test_close_clears_previous_failure(tmp_path):
    e = SF3Engine(tmp_path / "sfiii3n.zip")
    with pytest.raises(FileNotFoundError):
        e.start()
    e.close()
    assert e.state == "stopped" and e.error is None and e.latest is None


def test_menu_timeout_is_bounded_and_cleans_up(fake_engine):
    with pytest.raises(TimeoutError, match="menu frames"):
        fake_engine.start()
    assert fake_engine.state == "failed" and fake_engine._emu is None
    emu = FakeEmulator.instances[-1]
    assert emu.closed == 1 and len(emu.calls) < 1500
    assert any((":INPUTS", "2 Players Start") in fields for fields, _ in emu.calls)


def test_missing_rom_and_no_import_start_side_effect(tmp_path):
    e = SF3Engine(tmp_path / "sfiii3n.zip")
    with pytest.raises(FileNotFoundError):
        e.start()
    assert e.state == "failed"
    env = dict(os.environ, PYTHONPATH="backend")
    subprocess.run([sys.executable, "-c", "import sys; import jev_fly.engine; "
                    "assert 'MAMEToolkit' not in sys.modules"], env=env, check=True)


def test_fifo_binary_newlines_plus_signs_and_truncation(tmp_path):
    from jev_fly._mame import _Data
    pipe = _Data(tmp_path / "data.pipe", "dataPipe")
    raw = raw_state()
    payload = b"+".join(str(raw[k]).encode() for k in [*MEMORY, "emulated_frame"])
    payload += b"+" + raw["frame"].tobytes() + b"\n"

    def write():
        with open(pipe.path, "wb", buffering=0) as writer:
            writer.write(payload)

    writer = threading.Thread(target=write)
    try:
        writer.start()
        result = pipe.read_data()
        writer.join(2)
        assert not writer.is_alive()
        assert result["health1"] == -1 and result["x2"] == 601
        np.testing.assert_array_equal(result["frame"], raw["frame"])
        os.write(pipe.fd, b"1+2+truncated")
        with pytest.raises(TimeoutError):
            pipe.read_data(timeout=0.01)
    finally:
        pipe.close()


@pytest.mark.parametrize("after_pipes", [False, True])
def test_partial_constructor_failure_reaps_process_and_removes_runtime(monkeypatch, after_pipes):
    from jev_fly import _mame
    processes = []
    paths = []

    class BrokenConsole:
        def __init__(self, rom, root):
            paths.append(root)
            self.process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            processes.append(self.process)

        def writeln(self, *_args, **_kwargs):
            if not after_pipes:
                raise TimeoutError("resources unavailable")
            return "RESOURCES_READY"

        def close(self):
            self.process.terminate()
            self.process.wait(timeout=2)

    def broken_loop(emu):
        assert emu.actionPipe.path.exists() and emu.dataPipe.path.exists()
        raise TimeoutError("loop registration failed")

    monkeypatch.setattr(_mame, "_Console", BrokenConsole)
    monkeypatch.setattr(_mame.ManagedEmulator, "setup_frame_access_loop", broken_loop)
    with pytest.raises(TimeoutError):
        _mame.ManagedEmulator(Path("sfiii3n.zip"))
    assert processes[0].poll() is not None
    assert not paths[0].exists()


@pytest.mark.parametrize("flag", [None, "0", "true"])
async def test_http_controls_disabled_without_explicit_opt_in(
    aiohttp_client, fake_engine, monkeypatch, flag
):
    from jev_fly import app as app_module

    calls = []
    for operation in ("start", "reset", "close", "step"):
        monkeypatch.setattr(fake_engine, operation, lambda *args: calls.append(args))
    monkeypatch.setattr(app_module, "SF3Engine", lambda _: fake_engine)
    environment = {} if flag is None else {"SF3_ENABLE_LOCAL_CONTROL": flag}
    app = app_module.create_app(environ=environment)
    assert not any(route.method == "POST" for route in app.router.routes())
    client = await aiohttp_client(app)
    for command in ("start", "reset", "stop", "step"):
        path = f"/api/engine/{command}"
        response = await client.post(path, data="not even JSON",
                                     headers={"Origin": "https://untrusted.example"})
        assert response.status == 404
        assert await response.json() == {"error": "not_found", "path": path}
    status = await (await client.get("/api/engine")).json()
    health = await (await client.get("/api/health")).json()
    assert status["controls_enabled"] is False and status["state"] == "stopped"
    assert status["observation"] is None and health["engine"] == status
    assert (await client.get("/api/engine/frame")).status == 409
    assert calls == []  # No lifecycle/step dispatch before application cleanup.


async def test_http_runtime_controls_frames_failures_and_cleanup(aiohttp_client, fake_engine, monkeypatch):
    import io
    from PIL import Image
    from jev_fly import app as app_module

    monkeypatch.setattr(app_module, "SF3Engine", lambda _: fake_engine)
    monkeypatch.setattr(fake_engine, "_boot", lambda: fake_engine._step())
    client = await aiohttp_client(app_module.create_app(environ={"SF3_ENABLE_LOCAL_CONTROL": "1"}))
    status = await (await client.get("/api/engine")).json()
    assert status["controls_enabled"] is True
    assert status["state"] == "stopped" and status["observation"] is None
    assert (await client.get("/api/engine/frame")).status == 409
    assert (await client.post("/api/engine/step", json={})).status == 409
    for body in ({"p1": ["left", "right"]}, {"p2": ["invalid"]}, {"p1": "jab"},
                 {"typo": []}, {"p1": ["jab"] * 13}, {"p2": [None]}, {"p1": [["jab"]]}):
        assert (await client.post("/api/engine/step", json=body)).status == 400
    assert (await client.post("/api/engine/step", data=b"x" * 4097)).status == 413
    assert (await client.post("/api/engine/step", data=b"{broken")).status == 400
    assert (await client.post("/api/engine/no-such-operation")).status == 404
    response = await client.post("/api/engine/start")
    assert response.status == 200
    first = (await response.json())["observation"]
    response = await client.post("/api/engine/step", json={"p1": ["jab"], "p2": ["roundhouse"]})
    obs = (await response.json())["observation"]
    assert obs["sequence"] == first["sequence"] + 1 and obs["p2"]["health"] == 149
    assert fake_engine._emu.calls[-1][0] == [(":INPUTS", "P1 Jab Punch"), (":INPUTS", "P2 Roundhouse Kick")]
    frame = await client.get("/api/engine/frame")
    assert frame.status == 200 and frame.headers["X-Frame-Sequence"] == str(obs["sequence"])
    image = Image.open(io.BytesIO(await frame.read()))
    assert image.size == (384, 224) and image.getpixel((0, 0)) == (10, 43, 200)
    fake_engine._emu.fail = True
    assert (await client.post("/api/engine/step", json={})).status == 503
    health = await (await client.get("/api/health")).json()
    assert health["phase"] == 2 and health["engine"]["state"] == "failed"
    assert health["engine"]["observation"] is None
    assert (await client.post("/api/engine/reset")).status == 200
    assert (await client.post("/api/engine/start")).status == 200
    assert (await client.post("/api/engine/stop")).status == 200
    assert (await client.post("/api/engine/stop")).status == 200
    assert (await client.post("/api/engine/reset")).status == 200
    emu = fake_engine._emu
    await client.close()
    assert emu.closed == 1 and fake_engine.state == "stopped"


async def test_http_start_does_not_block_health(aiohttp_client, fake_engine, monkeypatch):
    import asyncio
    from jev_fly import app as app_module

    release = threading.Event()

    def boot():
        if not release.wait(2):
            raise TimeoutError("test did not release startup")
        fake_engine._step()

    monkeypatch.setattr(app_module, "SF3Engine", lambda _: fake_engine)
    monkeypatch.setattr(fake_engine, "_boot", boot)
    client = await aiohttp_client(app_module.create_app(environ={"SF3_ENABLE_LOCAL_CONTROL": "1"}))
    request = asyncio.create_task(client.post("/api/engine/start"))
    try:
        for _ in range(100):
            status = await (await client.get("/api/engine")).json()
            if status["state"] == "starting":
                break
        assert status["state"] == "starting" and not request.done()
    finally:
        release.set()
        await request


@pytest.mark.parametrize("error", [ValueError("private-path secret"), ImportError("private-path secret")])
async def test_http_engine_fault_is_not_bad_controls_or_disclosure(
    aiohttp_client, fake_engine, monkeypatch, error
):
    from jev_fly import app as app_module

    def boot():
        raise error

    monkeypatch.setattr(app_module, "SF3Engine", lambda _: fake_engine)
    monkeypatch.setattr(fake_engine, "_boot", boot)
    client = await aiohttp_client(app_module.create_app(environ={"SF3_ENABLE_LOCAL_CONTROL": "1"}))
    response = await client.post("/api/engine/start")
    assert response.status == 503
    assert await response.json() == {"error": "engine_failure"}
    status = await (await client.get("/api/engine")).json()
    assert status["state"] == "failed" and status["error"] == type(error).__name__
    assert "private-path" not in str(status) and "secret" not in str(status)
    assert fake_engine._emu is None and FakeEmulator.instances[-1].closed == 1


async def test_http_queued_step_after_stop_is_conflict(aiohttp_client, fake_engine, monkeypatch):
    import asyncio
    from jev_fly import app as app_module

    entered, release = threading.Event(), threading.Event()
    close = fake_engine.close

    def slow_close():
        entered.set()
        assert release.wait(3)
        close()

    monkeypatch.setattr(app_module, "SF3Engine", lambda _: fake_engine)
    monkeypatch.setattr(fake_engine, "_boot", lambda: fake_engine._step())
    fake_engine.start()
    monkeypatch.setattr(fake_engine, "close", slow_close)
    client = await aiohttp_client(app_module.create_app(environ={"SF3_ENABLE_LOCAL_CONTROL": "1"}))
    stop = asyncio.create_task(client.post("/api/engine/stop"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        step = asyncio.create_task(client.post("/api/engine/step", json={}))
        await asyncio.sleep(0.05)
        assert not step.done()
    finally:
        release.set()
        assert (await stop).status == 200
    response = await step
    assert response.status == 409
    assert await response.json() == {"error": "engine_not_running"}
    assert fake_engine.state == "stopped" and fake_engine.latest is None


def test_console_full_stdin_and_shared_deadline_are_bounded():
    import time
    from jev_fly._mame import _Console

    console = _Console.__new__(_Console)
    console.process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
    os.set_blocking(console.process.stdin.fileno(), False)
    os.set_blocking(console.process.stdout.fileno(), False)
    try:
        while True:
            try:
                os.write(console.process.stdin.fileno(), b"x" * 4096)
            except BlockingIOError:
                break
        console.deadline = time.monotonic() + 0.02
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="console write"):
            console.writeln("print(1)", timeout=5)
        assert time.monotonic() - started < 0.5
        # A new call cannot reset the already exhausted construction budget.
        with pytest.raises(TimeoutError, match="console write"):
            console.writeln("print(2)", timeout=5)
    finally:
        console.close()
    assert console.process.poll() is not None


def test_console_setup_failure_after_spawn_reaps_child(monkeypatch, tmp_path):
    from jev_fly import _mame

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def fail_setup(*_):
        raise OSError("cannot configure pipe")

    monkeypatch.setattr(_mame.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(_mame.os, "set_blocking", fail_setup)
    try:
        with pytest.raises(OSError, match="configure pipe"):
            _mame._Console(tmp_path / "sfiii3n.zip", tmp_path)
        assert process.poll() is not None
        assert process.stdin.closed and process.stdout.closed
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)


def test_reset_serializes_step_onto_new_process(fake_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    e = fake_engine
    monkeypatch.setattr(e, "_boot", lambda: e._step())
    first = e.start()
    old = e._emu
    entered, release, stepping = threading.Event(), threading.Event(), threading.Event()

    def boot():
        entered.set()
        assert release.wait(3)
        e._step()

    def step():
        stepping.set()
        return e.step(Controls(p2={B.DOWN}))

    monkeypatch.setattr(e, "_boot", boot)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            reset = pool.submit(e.reset)
            try:
                assert entered.wait(2)
                pending = pool.submit(step)
                assert stepping.wait(2) and not pending.done()
            finally:
                release.set()
            fresh = reset.result(timeout=2)
            obs = pending.result(timeout=2)
        assert old.closed == 1 and len(old.calls) == 1
        assert obs.sequence == fresh.sequence + 1 > first.sequence
        assert e._emu.calls[-1][0] == [(":INPUTS", "P2 Down")]
    finally:
        e.close()
