"""Opt-in proof: SF3_INTEGRATION=1 .venv/bin/pytest -s tests/test_engine_integration.py"""
import os
import signal
import subprocess
import tempfile
import time
from importlib.metadata import distribution
from pathlib import Path

import numpy as np
import pytest

from jev_fly.engine import Button as B, Controls, SF3Engine


@pytest.mark.skipif(os.environ.get("SF3_INTEGRATION") != "1", reason="requires user-supplied ROM")
def test_real_local_versus_twice_with_reset(monkeypatch):
    from jev_fly._mame import ManagedEmulator

    setup_loop = ManagedEmulator.setup_frame_access_loop

    def checked_loop(emu):
        emu.console.writeln('assert(iop.ports[":INPUTS"].fields["P2 Roundhouse Kick"]); '
                            'assert(iop.ports[":EXTRA"].fields["P2 Roundhouse Kick"] == nil)')
        print("live ioport: P2 Roundhouse Kick exists in :INPUTS, absent from :EXTRA")
        setup_loop(emu)

    monkeypatch.setattr(ManagedEmulator, "setup_frame_access_loop", checked_loop)
    rom = Path(os.environ.get("SFIII3_ROM_PATH", "sfiii3n.zip")).resolve()
    bundle = Path(distribution("MAMEToolkit").locate_file("MAMEToolkit/emulator/mame"))
    with tempfile.TemporaryDirectory(prefix="jev-audit-") as directory:
        audit = subprocess.run([str(bundle / "mame"), "-rompath", str(rom.parent), "-verifyroms", "sfiii3n"],
                               capture_output=True, text=True, timeout=30, cwd=directory,
                               env=dict(os.environ, FONTCONFIG_PATH=str(bundle / "fonts")))
    assert audit.returncode == 0, audit.stdout + audit.stderr
    assert "1 were OK" in audit.stdout
    print(audit.stdout.strip())
    e = SF3Engine(rom)
    old_sequence = 0
    processes, runtimes = [], []
    try:
        for run in (1, 2):
            started = time.monotonic()
            obs = e.start() if run == 1 else e.reset()
            elapsed = time.monotonic() - started
            processes.append(e._emu.console.process)
            runtimes.append(Path(e._emu.runtime.name))
            assert elapsed < 90 and obs.fighting
            assert obs.sequence > old_sequence
            assert (obs.p1.character, obs.p2.character, obs.p1.super_art, obs.p2.super_art) == (1, 2, 0, 0)
            assert (obs.p1.health, obs.p2.health, obs.timer) == (160, 160, 99)
            assert obs.p1.wins == obs.p2.wins == obs.p1.meter == obs.p2.meter == 0
            assert obs.frame.shape == (224, 384, 3) and obs.frame.std() > 10
            print(f"run={run} fight_seconds={elapsed:.2f} RGB={obs.frame.shape} mean={obs.frame.mean():.2f} "
                  f"health=160/160 timer=99 wins=0/0 characters=Alex/Ryu SA=I/I")
            for _ in range(40):
                obs = e.step()
            initial = obs
            for step in range(15):
                obs = e.step(Controls({B.RIGHT}, {B.UP}))
                if step == 0:
                    assert obs.p1.x > initial.p1.x  # No one-observation input lag.
            assert obs.p1.x > initial.p1.x + 30 and obs.p1.y == 0
            assert obs.p2.y > 20 and obs.p2.x == initial.p2.x
            assert not np.array_equal(initial.frame, obs.frame)
            print(f"simultaneous P1-right/P2-up: x1={initial.p1.x}->{obs.p1.x}; "
                  f"y2={initial.p2.y}->{obs.p2.y}; x2={obs.p2.x}")
            released_x = obs.p1.x
            # The game's input sampling can carry movement into one trailing
            # frame. A held direction would travel >20px in these ten frames.
            for _ in range(5):
                obs = e.step()
            assert 0 <= obs.p1.x - released_x < 10
            settled_x = obs.p1.x
            for step in range(25):
                obs = e.step()
                assert obs.p1.x == settled_x
                if step >= 10:
                    assert obs.p2.y == 0  # No second jump after landing.
            print(f"neutral releases both players: x1={obs.p1.x} held; y2={obs.p2.y}")
            for _ in range(12):
                obs = e.step(Controls({B.RIGHT}))
            hp = obs.p1.health
            for i in range(45):
                obs = e.step(Controls(p2={B.ROUNDHOUSE}) if i % 18 == 0 else Controls())
            assert obs.p1.health < hp and obs.p2.health == 160
            assert obs.p2.meter > 0 and obs.p1.stun > 0
            print(f"P2 roundhouse (:INPUTS): P1-health={hp}->{obs.p1.health}; "
                  f"P2-health={obs.p2.health}; meter={obs.p1.meter}/{obs.p2.meter}; stun={obs.p1.stun}/{obs.p2.stun}")
            start_obs = obs
            started = time.monotonic()
            for _ in range(150):
                previous = obs
                obs = e.step()
                assert obs.sequence == previous.sequence + 1
                assert obs.emulated_frame == previous.emulated_frame + 2
                assert obs.timestamp_ns > previous.timestamp_ns
            fps = 150 / (time.monotonic() - started)
            assert 20 <= fps <= 30.5, fps
            assert 3 <= start_obs.timer - obs.timer <= 7
            old_sequence = obs.sequence
            print(f"delivery_fps={fps:.2f} emulated_frames=300 timer={start_obs.timer}->{obs.timer}")
            if run == 2:
                assert processes[0].poll() is not None and not runtimes[0].exists()
                # A live but frozen child deadlocks stock FIFO reads. The adapter
                # must time out, escalate SIGTERM to SIGKILL, and reap it.
                processes[-1].send_signal(signal.SIGSTOP)
                stalled = time.monotonic()
                with pytest.raises(TimeoutError, match="frame pipe timed out"):
                    e.step()
                assert time.monotonic() - stalled < 7
                assert e.state == "failed" and e.latest is None
                assert processes[-1].returncode == -signal.SIGKILL
                print("frozen-child frame timeout + kill/reap <7s; stale observation cleared")
    finally:
        e.close()
        e.close()
    assert all(p.poll() is not None for p in processes)
    assert all(not p.exists() for p in runtimes)
    print(f"clean_exit=True processes_reaped={len(processes)} runtime_dirs_and_fifos_remaining=0")
