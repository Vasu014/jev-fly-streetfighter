from __future__ import annotations

import asyncio
import io
import os
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Mapping

from aiohttp import web
from PIL import Image

from .engine import Controls, SF3Engine

from .health import (
    RuntimeCheck,
    VersionLookup,
    build_health,
    flybrain_runtime_importable,
    mame_binary_available,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _json_not_found(path: str) -> web.Response:
    return web.json_response(
        {"error": "not_found", "path": path},
        status=404,
    )


def create_app(
    *,
    static_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
    version_lookup: VersionLookup = version,
    runtime_check: RuntimeCheck = flybrain_runtime_importable,
    mame_binary_check: RuntimeCheck = mame_binary_available,
) -> web.Application:
    assets_root = (static_dir or PROJECT_ROOT / "frontend" / "dist").resolve()
    environment = os.environ if environ is None else environ
    controls_enabled = environment.get("SF3_ENABLE_LOCAL_CONTROL") == "1"
    app = web.Application(client_max_size=4096)
    engine = SF3Engine(Path(environment.get("SFIII3_ROM_PATH", PROJECT_ROOT / "sfiii3n.zip")))
    engine_lock = asyncio.Lock()

    def engine_status():
        obs = engine.latest
        return {"state": engine.state, "error": engine.error,
                "controls_enabled": controls_enabled,
                "delivery": "pull", "target_fps": 29.8,
                "observation": None if obs is None else {
                    "sequence": obs.sequence, "emulated_frame": obs.emulated_frame,
                    "timestamp_ns": obs.timestamp_ns, "fighting": obs.fighting,
                    "timer": obs.timer, "p1": asdict(obs.p1), "p2": asdict(obs.p2),
                    "frame": {"width": 384, "height": 224, "format": "RGB"}}}

    async def run_engine(operation, *args):
        # Hold the gate until the worker completes, even if the HTTP client leaves.
        async with engine_lock:
            if operation == engine.step and engine.state != "running":
                raise web.HTTPConflict(text='{"error": "engine_not_running"}',
                                       content_type="application/json")
            task = asyncio.create_task(asyncio.to_thread(operation, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def runtime(_: web.Request):
        return web.json_response(engine_status())

    async def control(request: web.Request):
        command = request.match_info["command"]
        operations = {"start": engine.start, "reset": engine.reset, "stop": engine.close}
        if command == "step":
            try:
                body = await request.json()
                if not isinstance(body, dict) or set(body) - {"p1", "p2"}:
                    raise ValueError("Expected p1/p2 button arrays")
                if any(not isinstance(buttons, list) or len(buttons) > 12
                       for buttons in body.values()):
                    raise ValueError("Expected at most 12 buttons per player")
                controls = Controls(body.get("p1", []), body.get("p2", []))
            except (ValueError, TypeError):
                return web.json_response({"error": "invalid_controls"}, status=400)
        try:
            if command == "step":
                await run_engine(engine.step, controls)
            elif command in operations:
                await run_engine(operations[command])
            else:
                return _json_not_found(request.path)
        except web.HTTPException:
            raise
        except Exception:
            return web.json_response({"error": "engine_failure"}, status=503)
        return web.json_response(engine_status())

    async def frame(_: web.Request):
        obs = engine.latest
        if obs is None:
            return web.json_response({"error": "frame_unavailable"}, status=409)
        buffer = io.BytesIO()
        Image.fromarray(obs.frame).save(buffer, format="PNG")
        return web.Response(body=buffer.getvalue(), content_type="image/png", headers={
            "Cache-Control": "no-store", "X-Frame-Sequence": str(obs.sequence),
            "X-Frame-Timestamp-Ns": str(obs.timestamp_ns),
        })

    async def cleanup(_: web.Application):
        await run_engine(engine.close)

    app.on_cleanup.append(cleanup)

    async def health(_: web.Request) -> web.Response:
        payload = build_health(
                environ=environment,
                project_root=PROJECT_ROOT,
                version_lookup=version_lookup,
                runtime_check=runtime_check,
                mame_binary_check=mame_binary_check,
            )
        payload["phase"] = 2
        payload["engine"] = engine_status()
        return web.json_response(payload)

    async def api_not_found(request: web.Request) -> web.Response:
        return _json_not_found(request.path)

    async def frontend(request: web.Request) -> web.StreamResponse:
        if not assets_root.is_dir():
            return web.json_response(
                {
                    "error": "frontend_not_built",
                    "detail": "Run `npm --prefix frontend run build`.",
                },
                status=503,
            )

        relative_path = request.match_info.get("path", "")
        requested = (assets_root / relative_path).resolve()
        if not requested.is_relative_to(assets_root):
            raise web.HTTPNotFound()
        if requested.is_file():
            return web.FileResponse(requested)

        # Missing files (especially Vite assets) are errors, not client-side routes.
        if (
            relative_path == "assets"
            or relative_path.startswith("assets/")
            or Path(relative_path).suffix
        ):
            raise web.HTTPNotFound()

        return web.FileResponse(assets_root / "index.html")

    app.router.add_get("/api/health", health)
    app.router.add_get("/api/engine", runtime)
    app.router.add_get("/api/engine/frame", frame)
    if controls_enabled:
        app.router.add_post("/api/engine/{command}", control)
    app.router.add_route("*", "/api", api_not_found)
    app.router.add_route("*", "/api/{path:.*}", api_not_found)
    app.router.add_get("/{path:.*}", frontend)
    return app
