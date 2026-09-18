from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from jev_fly import create_app


PINNED_VERSIONS = {
    "MAMEToolkit": "1.1.0",
    "Brian2": "2.5.1",
    "numpy": "1.24.0",
    "Cython": "0.29.36",
}


def version_lookup(package: str) -> str:
    try:
        return PINNED_VERSIONS[package]
    except KeyError as error:
        raise PackageNotFoundError(package) from error


@pytest.fixture
def built_frontend(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<main>application shell</main>")
    (tmp_path / "assets" / "app-abc.js").write_text("window.shellLoaded = true")
    return tmp_path


async def test_health_reports_optional_dependencies_without_exposing_key(
    aiohttp_client, built_frontend: Path, tmp_path: Path
):
    missing_rom = tmp_path / "missing-sfiii3n.zip"
    secret = "must-never-appear"
    app = create_app(
        static_dir=built_frontend,
        environ={
            "SFIII3_ROM_PATH": str(missing_rom),
            "TYPESAFE_API_KEY": secret,
        },
        version_lookup=version_lookup,
        runtime_check=lambda: True,
        mame_binary_check=lambda: True,
    )
    client = await aiohttp_client(app)

    response = await client.get("/api/health")
    payload = await response.json()

    assert response.status == 200
    assert payload["status"] == "ok"
    assert payload["ready"] is True
    assert payload["dependencies"]["mame"] == {
        "status": "rom_not_configured",
        "configured": False,
        "ready": False,
        "checked": "package_binary_and_rom_presence",
        "toolkit_version": "1.1.0",
        "binary_present": True,
        "rom": {
            "name": "sfiii3n",
            "present": False,
        },
        "detail": "Passive prerequisite check; see engine for live runtime status.",
    }
    assert payload["dependencies"]["flybrain"]["status"] == "dependencies_ready"
    assert payload["dependencies"]["flybrain"]["ready"] is False
    assert payload["dependencies"]["typesafe"] == {
        "status": "configured_unchecked",
        "configured": True,
        "reachable": None,
        "checked": False,
        "api_base": "https://api.typesafe.ai",
        "detail": "No request is made by health checks; connectivity remains unchecked.",
    }
    assert secret not in await response.text()
    assert str(missing_rom) not in await response.text()


async def test_health_does_not_claim_mame_ready_when_binary_is_missing(
    aiohttp_client, built_frontend: Path, tmp_path: Path
):
    rom = tmp_path / "sfiii3n.zip"
    rom.touch()
    client = await aiohttp_client(
        create_app(
            static_dir=built_frontend,
            environ={"SFIII3_ROM_PATH": str(rom)},
            version_lookup=version_lookup,
            runtime_check=lambda: True,
            mame_binary_check=lambda: False,
        )
    )

    payload = await (await client.get("/api/health")).json()

    assert payload["ready"] is True
    assert payload["dependencies"]["mame"]["status"] == "binary_unavailable"
    assert payload["dependencies"]["mame"]["binary_present"] is False
    assert payload["dependencies"]["mame"]["rom"]["present"] is True
    assert payload["dependencies"]["mame"]["ready"] is False


async def test_static_assets_spa_fallback_and_api_404_semantics(
    aiohttp_client, built_frontend: Path
):
    outside_file = built_frontend.parent / "secret"
    outside_file.write_text("must not be served")
    (built_frontend / "escape").symlink_to(outside_file)
    client = await aiohttp_client(create_app(static_dir=built_frontend, environ={}))

    root = await client.get("/")
    asset = await client.get("/assets/app-abc.js")
    spa_route = await client.get("/match/round/1")
    missing_asset = await client.get("/assets/missing.js")
    extensionless_missing_asset = await client.get("/assets/missing")
    traversal = await client.get("/escape")
    api_root = await client.get("/api")
    missing_api = await client.get("/api/does-not-exist")

    assert root.status == 200
    assert await root.text() == "<main>application shell</main>"
    assert asset.status == 200
    assert "shellLoaded" in await asset.text()
    assert spa_route.status == 200
    assert await spa_route.text() == "<main>application shell</main>"
    assert missing_asset.status == 404
    assert extensionless_missing_asset.status == 404
    assert traversal.status == 404
    assert api_root.status == 404
    assert await api_root.json() == {"error": "not_found", "path": "/api"}
    assert missing_api.status == 404
    assert await missing_api.json() == {
        "error": "not_found",
        "path": "/api/does-not-exist",
    }


async def test_missing_production_build_is_an_explicit_service_error(
    aiohttp_client, tmp_path: Path
):
    client = await aiohttp_client(
        create_app(static_dir=tmp_path / "not-built", environ={})
    )

    response = await client.get("/")

    assert response.status == 503
    assert (await response.json())["error"] == "frontend_not_built"
