"""Private bounded transport for MAMEToolkit 1.1.0's Emulator Lua protocol.

The stock constructor has unbounded console/FIFO opens and global writable
paths. Keep its action/frame loop, but own process, FIFOs, and runtime paths.
Loaded only when SF3Engine.start is explicitly called.
"""
from __future__ import annotations

import os
import select
import subprocess
import tempfile
import time
import uuid
from importlib.metadata import distribution
from pathlib import Path

import numpy as np
from MAMEToolkit.emulator import Action, Address, Emulator

from .engine import Button, Controls, MEMORY, input_fields


class _Console:
    def __init__(self, rom: Path, runtime: Path):
        self.deadline = time.monotonic() + 20
        bundle = Path(distribution("MAMEToolkit").locate_file("MAMEToolkit/emulator/mame"))
        env = dict(os.environ, FONTCONFIG_PATH=str(bundle / "fonts"), SDL_VIDEODRIVER="dummy")
        self.process = subprocess.Popen(
            [str(bundle / "mame"), "sfiii3n", "-rompath", str(rom.parent),
             "-pluginspath", str(bundle / "plugins"), "-skip_gameinfo", "-console",
             "-video", "none", "-sound", "none", "-throttle", "-frameskip", "0",
             "-noreadconfig"], cwd=runtime, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0,
        )
        try:
            os.set_blocking(self.process.stdout.fileno(), False)
            os.set_blocking(self.process.stdin.fileno(), False)
        except BaseException:
            self.close()
            raise

    def writeln(self, command, expect_output=False, timeout=5, **_):
        marker = "DONE" + uuid.uuid4().hex
        payload = ('local ok, err = pcall(function() ' + command +
                   ' end); if not ok then print("error: "..tostring(err)) end; print("' + marker + '")\n').encode()
        if len(payload) > 4096:
            raise ValueError("Console command exceeds atomic pipe limit")
        # All console commands belong to construction. One shared deadline
        # prevents 25 individually slow field checks from exceeding startup.
        deadline = min(time.monotonic() + timeout, self.deadline)
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([], [self.process.stdin], [], left)[1]:
            raise TimeoutError("MAME console write timed out")
        if os.write(self.process.stdin.fileno(), payload) != len(payload):
            raise RuntimeError("Short console write")
        output = bytearray()
        while marker.encode() not in output:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("MAME console command timed out: " + output.decode(errors="replace")[-1000:])
            if not select.select([self.process.stdout], [], [], left)[0]:
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("MAME exited: " + output.decode(errors="replace")[-1000:])
            output.extend(chunk)
        text = output.decode(errors="replace")
        if "error:" in text.lower() or "error in" in text.lower():
            raise RuntimeError(text[-1000:])
        if expect_output:
            return text

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process.wait(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()


class _Pipe:
    def __init__(self, path: Path, name: str):
        self.path, self.name = path, name
        os.mkfifo(path, 0o600)
        # O_RDWR prevents either peer's open from blocking during partial startup.
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def get_lua_string(self):
        return f"{self.name}:read(); "

    def writeln(self, data: str):
        payload = (data + "\n").encode()
        if len(payload) > 4096:
            raise ValueError("Action packet exceeds atomic FIFO limit")
        if not select.select([], [self.fd], [], 2)[1]:
            raise TimeoutError("MAME action pipe timed out")
        if os.write(self.fd, payload) != len(payload):
            raise RuntimeError("Short action write")

    def close(self):
        os.close(self.fd)


class _Data(_Pipe):
    def get_lua_string(self):
        values = [Address(hex(address), mode).get_lua_string() for address, mode in MEMORY.values()]
        values += ["s:frame_number()", "s:bitmap_binary()"]
        return 'dataPipe:write(' + '.."+"..'.join(values) + '.."\\n"); dataPipe:flush(); '

    def read_data(self, timeout=3):
        deadline = time.monotonic() + min(timeout, 3)
        packet = bytearray()
        header_size = None
        while True:
            if header_size is None and packet.count(b"+") >= len(MEMORY) + 1:
                header_size = len(packet) - len(packet.split(b"+", len(MEMORY) + 1)[-1])
            if header_size is not None and len(packet) == header_size + 384 * 224 * 3 + 1:
                break
            if len(packet) > 384 * 224 * 3 + 2048:
                raise RuntimeError("Malformed MAME frame packet")
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], max(0, left))[0]:
                raise TimeoutError("MAME frame pipe timed out")
            packet.extend(os.read(self.fd, 262144))
        if packet[-1:] != b"\n":
            raise RuntimeError("Missing MAME frame delimiter")
        parts = packet.split(b"+", len(MEMORY) + 1)
        raw = dict(zip([*MEMORY, "emulated_frame"], map(int, parts[:-1])))
        raw["frame"] = np.frombuffer(parts[-1][:-1], dtype=np.uint8).reshape(224, 384, 3)
        return raw


class ManagedEmulator(Emulator):
    def __init__(self, rom: Path):
        self.runtime = tempfile.TemporaryDirectory(prefix="jev-sf3-")
        self.console = self.actionPipe = self.dataPipe = None
        self.frame_ratio = 2
        self.first = True
        try:
            root = Path(self.runtime.name)
            self.console = _Console(rom, root)
            deadline = time.monotonic() + 20
            while True:
                result = self.console.writeln('if manager:machine().devices[":maincpu"] and '
                                               'manager:machine().screens[":screen"] then print("RESOURCES_READY") end',
                                               expect_output=True)
                if "RESOURCES_READY" in result:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("MAME resources failed to register")
            self.console.writeln('iop = manager:machine():ioport(); s = manager:machine().screens[":screen"]; '
                                 'mem = manager:machine().devices[":maincpu"].spaces["program"]; releaseQueue = {}', timeout=20)
            # Runtime validation includes the asymmetric bundled P2 HK port.
            fields = input_fields(Controls(set(Button) - {Button.DOWN, Button.LEFT},
                                           set(Button) - {Button.DOWN, Button.LEFT}))
            fields += input_fields(Controls({Button.DOWN, Button.LEFT}, {Button.DOWN, Button.LEFT}))
            fields += [(":INPUTS", "Service Mode")]
            for port, field in fields:
                self.console.writeln(f'assert(iop.ports["{port}"].fields["{field}"], "missing input {field}")')
            self.console.writeln('assert(s:width() == 384 and s:height() == 224, "unexpected screen dimensions")')
            self.actionPipe = _Pipe(root / "action.pipe", "actionPipe")
            self.dataPipe = _Data(root / "data.pipe", "dataPipe")
            self.console.writeln(f'actionPipe = assert(io.open("{root}/action.pipe", "r")); '
                                 f'dataPipe = assert(io.open("{root}/data.pipe", "w"))')
            self.setup_frame_access_loop()
        except BaseException:
            self.close()
            raise

    def sample(self, fields, *, service=False):
        if self.console.process.poll() is not None:
            raise RuntimeError("MAME process exited unexpectedly")
        if service:
            fields = [*fields, (":INPUTS", "Service Mode")]
        return super().step([Action(port, field) for port, field in fields])

    def close(self):
        if self.console is not None:
            self.console.close()
            self.console = None
        for name in ("actionPipe", "dataPipe"):
            pipe = getattr(self, name)
            if pipe is not None:
                pipe.close()
                setattr(self, name, None)
        self.runtime.cleanup()
