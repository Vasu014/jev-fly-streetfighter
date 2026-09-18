from __future__ import annotations

import os
from importlib import import_module
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from typing import Callable, Mapping


VersionLookup = Callable[[str], str]
RuntimeCheck = Callable[[], bool]


def _installed_version(package: str, lookup: VersionLookup) -> str | None:
    try:
        return lookup(package)
    except PackageNotFoundError:
        return None


def flybrain_runtime_importable() -> bool:
    try:
        import_module("brian2")
    except Exception:
        return False
    return True


def mame_binary_available() -> bool:
    try:
        binary = distribution("MAMEToolkit").locate_file(
            "MAMEToolkit/emulator/mame/mame"
        )
    except (PackageNotFoundError, OSError):
        return False
    return binary.is_file() and os.access(binary, os.X_OK)


def build_health(
    *,
    environ: Mapping[str, str],
    project_root: Path,
    version_lookup: VersionLookup = version,
    runtime_check: RuntimeCheck = flybrain_runtime_importable,
    mame_binary_check: RuntimeCheck = mame_binary_available,
) -> dict[str, object]:
    toolkit_version = _installed_version("MAMEToolkit", version_lookup)
    runtime_versions = {
        "brian2": _installed_version("Brian2", version_lookup),
        "numpy": _installed_version("numpy", version_lookup),
        "cython": _installed_version("Cython", version_lookup),
    }

    rom_path = Path(environ.get("SFIII3_ROM_PATH", project_root / "sfiii3n.zip"))
    rom_present = rom_path.is_file()
    mame_binary_present = toolkit_version == "1.1.0" and mame_binary_check()
    mame_ready = mame_binary_present and rom_present
    if toolkit_version != "1.1.0":
        mame_status = "toolkit_unavailable"
    elif not mame_binary_present:
        mame_status = "binary_unavailable"
    elif not rom_present:
        mame_status = "rom_not_configured"
    else:
        mame_status = "prerequisites_ready"

    flybrain_dependencies_ready = runtime_versions == {
        "brian2": "2.5.1",
        "numpy": "1.24.0",
        "cython": "0.29.36",
    } and runtime_check()
    typesafe_configured = bool(environ.get("TYPESAFE_API_KEY"))

    return {
        "status": "ok",
        "ready": True,
        "phase": 2,
        "dependencies": {
            "mame": {
                "status": mame_status,
                "configured": rom_present,
                "ready": mame_ready,
                "checked": "package_binary_and_rom_presence",
                "toolkit_version": toolkit_version,
                "binary_present": mame_binary_present,
                "rom": {
                    "name": "sfiii3n",
                    "present": rom_present,
                },
                "detail": "Passive prerequisite check; see engine for live runtime status.",
            },
            "flybrain": {
                "status": (
                    "dependencies_ready"
                    if flybrain_dependencies_ready
                    else "dependencies_unavailable"
                ),
                "configured": flybrain_dependencies_ready,
                "ready": False,
                "checked": "package_versions_and_runtime_import",
                "versions": runtime_versions,
                "detail": "The FlyBrain model is not included or launched during Phase 1.",
            },
            "typesafe": {
                "status": "configured_unchecked" if typesafe_configured else "not_configured",
                "configured": typesafe_configured,
                "reachable": None,
                "checked": False,
                "api_base": (
                    "custom"
                    if environ.get("TYPESAFE_BASE_URL")
                    else "https://api.typesafe.ai"
                ),
                "detail": (
                    "No request is made by health checks; connectivity remains unchecked."
                ),
            },
        },
    }
