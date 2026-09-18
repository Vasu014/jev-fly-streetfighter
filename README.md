# Jev × FlyBrain Street Fighter

Phase 2 adds a real local-versus `SF3Engine` around MAMEToolkit/MAME. The aiohttp backend still starts without launching an emulator, loading FlyBrain, or calling TypeSafe. An explicit engine start boots Alex versus Ryu (Super Art I for both); serialized steps return live RAM and RGB frames at approximately 29.8 FPS. See [engine contracts, provenance, and integration proof](docs/emulator.md).

## Fresh Amp Orb

```bash
.agents/setup
amp orb services ensure
```

Setup installs CPython 3.10.21, synchronizes exact Python dependency pins (including MAMEToolkit 1.1.0, Brian2 2.5.1, NumPy 1.24.0, Cython 0.29.36, and Pillow), installs the locked npm graph, and builds the frontend. The declared `app` service exposes one portal and uses `/api/health` for readiness.

HTTP engine controls are disabled by default (404). The portal service explicitly sets `SF3_ENABLE_LOCAL_CONTROL=0` and stays read-only even if the parent environment opts in. Only `SF3_ENABLE_LOCAL_CONTROL=1` enables mutation routes for an isolated trusted-local coordinator; see the [opt-in command and security limitations](docs/emulator.md#python-and-http-ownership). This flag is not authentication: do not enable it on a public portal.

The validated ROM is intentionally not stored by this scaffold. Put `sfiii3n.zip` at the repository root or set `SFIII3_ROM_PATH` to its location; if absent, health reports `rom_not_configured` while the application remains available.

`TYPESAFE_API_KEY` remains optional. Health only reports whether it is configured. It never displays the key or sends a model request. [TypeSafe's current API docs](https://docs.typesafe.ai/api) document `https://api.typesafe.ai` as the API base; no separate free health endpoint is documented, so reachability stays `null` / unchecked.

## Local commands

```bash
# Build and run the production service
npm --prefix frontend run build
PYTHONPATH=backend .venv/bin/python -m jev_fly --port 8080

# Verify
PYTHONPATH=backend .venv/bin/pytest
npm --prefix frontend test
npm --prefix frontend run build

# Explicit real-ROM proof: audits ROM, boots twice, tests independent inputs,
# verifies FPS/RAM/damage/reset/cleanup. Requires a user-supplied ROM.
SF3_INTEGRATION=1 .venv/bin/pytest -s tests/test_engine_integration.py
```

For frontend-only iteration, run `npm --prefix frontend run dev`; Vite proxies `/api` to an aiohttp service on port 8080.
