"""The lane's routes: owner check first, 404 while the preview is off, own ids only."""

from __future__ import annotations

import asyncio
import logging
import stat
import sys

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from managed_env_fake_box import FakeBox, FakeRegistry
from managed_env_fake_service import FakeService

from kiro_crew.managed_env.client import Credential, SandboxClient
from kiro_crew.managed_env.engine import SandboxLaunchEngine
from kiro_crew.managed_env.guard import SandboxGuard
from kiro_crew.managed_env.remote import ssh_host_for
from kiro_crew.managed_env.routes import SandboxRoutes, register_sandbox_routes
from kiro_crew.managed_env.settings import LaneSettings

ROUTES = [
    ("GET", "/api/managed-environments/list"),
    ("POST", "/api/managed-environments/env-001/stop"),
    ("POST", "/api/managed-environments/env-001/resume"),
    ("POST", "/api/managed-environments/env-001/delete"),
    ("POST", "/api/managed-environments/env-001/ssh-session"),
    ("GET", "/api/managed-environments/ssh-config"),
    ("POST", "/api/managed-environments/ssh-config"),
]


@pytest.fixture
def svc():
    f = FakeService()
    yield f
    f.close()


@pytest.fixture
def lane(svc, tmp_path, monkeypatch):
    from kiro_crew.dashboard.handlers import _shared

    state = {"on": True, "owner": True, "host": True}

    async def owner(request, operation):
        return None if state["owner"] else web.json_response({"error": "forbidden"}, status=403)

    monkeypatch.setattr(_shared, "require_owner_dashboard_request", owner)
    monkeypatch.setattr(
        "kiro_crew.dashboard.handlers.source_providers.is_host_owner_dashboard_request",
        lambda request: state["host"],
    )
    settings = LaneSettings(endpoint=svc.endpoint, ssh_region="ex-test-1")
    client = SandboxClient(
        lambda: Credential("tok-secret", "a"),
        SandboxGuard(tmp_path / "s"),
        settings=lambda: settings,
    )
    engine = SandboxLaunchEngine(
        client=client, registry_factory=FakeRegistry, run=FakeBox(), settings=lambda: settings
    )
    routes = SandboxRoutes(lambda: engine, lane_enabled=lambda: state["on"])
    return routes, client, state


def call(routes: SandboxRoutes, method: str, path: str):
    async def go():
        app = web.Application()
        register_sandbox_routes(app, routes)
        async with TestClient(TestServer(app)) as c:
            resp = await c.request(method, path)
            return resp.status, await resp.json()

    return asyncio.run(go())


def _own(client, svc) -> str:
    sid = client.create_sandbox("crew-a-kc-0a1b2c")["execEnvironmentId"]
    svc.sandboxes[sid]["status"] = "RUNNING"
    return sid


@pytest.mark.parametrize("method, path", ROUTES)
def test_every_route_checks_the_owner_first_then_answers_404_while_off(lane, svc, method, path):
    routes, _, state = lane
    state["owner"] = False
    assert call(routes, method, path)[0] == 403
    state["owner"], state["on"] = True, False
    assert call(routes, method, path)[0] == 404
    assert svc.requests == []


@pytest.mark.parametrize("method, path", ROUTES)
def test_a_teammate_who_passes_the_owner_gate_is_refused(lane, svc, method, path):
    routes, _, state = lane
    state["host"] = False
    status, body = call(routes, method, path)
    assert status == 403 and body["code"] == "owner_only"
    assert svc.requests == []


def test_the_list_marks_own_environments(lane, svc):
    routes, client, _ = lane
    sid = _own(client, svc)
    other = svc.add("RUNNING", "elsewhere")
    status, body = call(routes, "GET", "/api/managed-environments/list")
    assert status == 200
    items = {i["sandbox_id"]: i for i in body["sandboxes"]}
    assert items[sid]["own"] is True and items[sid]["ssh_host"] == f"crew-sbx-{sid}"
    assert items[other]["own"] is False and "ssh_host" not in items[other]


@pytest.mark.parametrize("action", ["stop", "resume", "delete", "ssh-session"])
def test_another_clients_environment_takes_no_action(lane, svc, action):
    routes, *_ = lane
    other = svc.add("RUNNING", "elsewhere")
    status, body = call(routes, "POST", f"/api/managed-environments/{other}/{action}")
    assert status == 409 and body["code"] == "not_own_id"
    assert svc.ops() == []


def test_stop_resume_and_delete_an_own_environment(lane, svc):
    routes, client, _ = lane
    sid = _own(client, svc)
    assert call(routes, "POST", f"/api/managed-environments/{sid}/stop") == (
        200,
        {"status": "STOPPING"},
    )
    assert call(routes, "POST", f"/api/managed-environments/{sid}/resume")[0] == 200
    assert call(routes, "POST", f"/api/managed-environments/{sid}/delete") == (
        200,
        {"deleted": True},
    )
    assert call(routes, "POST", "/api/managed-environments/env-x/reboot")[0] == 404


def test_a_deleting_environment_stays_own_until_a_list_shows_it_gone(lane, svc, monkeypatch):
    routes, client, _ = lane
    reg = FakeRegistry()
    engine = routes._engine()
    monkeypatch.setattr(engine, "_registry_factory", lambda: reg)
    sid = _own(client, svc)
    reg.add(name="crew-a", ssh_host=ssh_host_for(sid))
    deleting = {"sandboxes": [{"execEnvironmentId": sid, "status": "DELETING"}]}
    svc.answer("ListSandboxes", 200, deleting)  # the delete's own read-back
    svc.answer("ListSandboxes", 200, deleting)  # the dashboard's next List
    assert call(routes, "POST", f"/api/managed-environments/{sid}/delete") == (
        200,
        {"deleted": False},
    )
    _, body = call(routes, "GET", "/api/managed-environments/list")
    assert body["sandboxes"] == [
        {"sandbox_id": sid, "status": "DELETING", "own": True, "ssh_host": ssh_host_for(sid)}
    ]
    assert len(reg.list()) == 1
    _, body = call(routes, "GET", "/api/managed-environments/list")
    assert body["sandboxes"] == [] and reg.list() == []


def test_a_deleted_crews_tunnel_is_stopped_before_its_row_goes(lane, svc, monkeypatch):
    routes, client, _ = lane
    reg = FakeRegistry()
    engine = routes._engine()
    monkeypatch.setattr(engine, "_registry_factory", lambda: reg)
    sid = _own(client, svc)
    row = reg.add(name="crew-a", ssh_host=ssh_host_for(sid))
    order: list[str] = []

    class Manager:
        async def disconnect(self, crew_id, keep_intent=False):
            order.append(f"stop {crew_id} rows={len(reg.list())}")
            return True

    async def go():
        app = web.Application()
        app["state"] = type("S", (), {"instances_manager": Manager()})()
        register_sandbox_routes(app, routes)
        async with TestClient(TestServer(app)) as c:
            resp = await c.post(f"/api/managed-environments/{sid}/delete")
            return resp.status, await resp.json()

    assert asyncio.run(go()) == (200, {"deleted": True})
    assert order == [f"stop {row.id} rows=1"] and reg.list() == []


def test_rows_are_dropped_under_the_core_chain_lock(lane, svc, monkeypatch):
    """A chained crew added meanwhile cannot slip in between the cascade's read and write."""
    from kiro_crew.dashboard.handlers_instances_gate import _CHAIN_MUTATION_LOCK

    routes, client, _ = lane
    reg = FakeRegistry()
    engine = routes._engine()
    monkeypatch.setattr(engine, "_registry_factory", lambda: reg)
    sid = _own(client, svc)
    reg.add(name="crew-a", ssh_host=ssh_host_for(sid))
    held: list[bool] = []
    real = engine.forget_row

    def forget(s, **kw):
        held.append(_CHAIN_MUTATION_LOCK.locked())
        return real(s, **kw)

    monkeypatch.setattr(engine, "forget_row", forget)
    assert call(routes, "POST", f"/api/managed-environments/{sid}/delete") == (
        200,
        {"deleted": True},
    )
    assert held == [True] and reg.list() == []


def test_the_ssh_settings_are_read_and_written_with_the_gateways_python(
    lane, tmp_path, monkeypatch
):
    routes, _, _ = lane
    cfg = tmp_path / "ssh" / "config"
    monkeypatch.setattr("kiro_crew.managed_env.ssh_config.default_config", lambda: str(cfg))
    from kiro_crew.managed_env import ssh_config

    block = ssh_config.render_block(ssh_config.gateway_python())
    assert call(routes, "GET", "/api/managed-environments/ssh-config") == (
        200,
        {"present": False, "path": str(cfg), "block": block},
    )
    assert not cfg.exists()  # a read writes nothing
    assert call(routes, "POST", "/api/managed-environments/ssh-config") == (
        200,
        {"present": True, "path": str(cfg), "block": block},
    )
    # what was shown is what was written
    assert cfg.read_text(encoding="utf-8").startswith(block)
    assert sys.executable in cfg.read_text(encoding="utf-8")

    cfg.write_text(ssh_config.BEGIN + "\n", encoding="utf-8")  # a damaged block
    status, body = call(routes, "POST", "/api/managed-environments/ssh-config")
    assert status == 409 and body["code"] == "ssh_config_unwritable"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_ssh_settings_write_keeps_other_entries_and_mode_0600(lane, tmp_path, monkeypatch):
    from kiro_crew.managed_env import ssh_config

    routes, _, _ = lane
    (tmp_path / "home").mkdir()
    cfg = tmp_path / "home" / "config"
    mine = "Host work\n  HostName work.example\n  User me\n\nHost *\n  ServerAliveInterval 30\n"
    cfg.write_text(mine, encoding="utf-8")
    cfg.chmod(0o600)
    monkeypatch.setattr("kiro_crew.managed_env.ssh_config.default_config", lambda: str(cfg))
    assert call(routes, "POST", "/api/managed-environments/ssh-config")[0] == 200
    text = cfg.read_text(encoding="utf-8")
    assert text.count(ssh_config.BEGIN) == 1 and text.count(ssh_config.END) == 1
    # outside the Crew-marked block the file is the user's, byte for byte
    start, end = text.index(ssh_config.BEGIN), text.index(ssh_config.END) + len(ssh_config.END) + 1
    assert (text[:start] + text[end:]).lstrip("\n") == mine
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    # a second write changes nothing
    assert call(routes, "POST", "/api/managed-environments/ssh-config")[0] == 200
    assert cfg.read_text(encoding="utf-8") == text
    assert [p.name for p in cfg.parent.iterdir()] == ["config"]  # no temp file left


def test_the_ssh_settings_routes_are_404_on_an_lts_build(tmp_path, monkeypatch):
    """The real preview switch, not a stub: an LTS build reads ``crew_client`` off."""
    import json
    from unittest.mock import patch

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"dashboard": {"feature_previews": {"crew_client": True}}}), encoding="utf-8"
    )
    monkeypatch.setattr("kiro_crew.config.live.snapshot", lambda: None)
    monkeypatch.setattr(
        "kiro_crew.managed_env.ssh_config.default_config", lambda: str(tmp_path / "ssh")
    )
    from kiro_crew.dashboard.handlers import _shared

    async def owner(request, operation):
        return None

    monkeypatch.setattr(_shared, "require_owner_dashboard_request", owner)
    monkeypatch.setattr(
        "kiro_crew.dashboard.handlers.source_providers.is_host_owner_dashboard_request",
        lambda request: True,
    )
    routes = SandboxRoutes(lambda: pytest.fail("no engine while off"))
    with patch("kiro_crew.config.loader.config_path", return_value=cfg_path):
        assert call(routes, "GET", "/api/managed-environments/ssh-config")[0] == 200
        monkeypatch.setattr("kiro_crew.edition.EDITION", "lts")
        for method in ("GET", "POST"):
            assert call(routes, method, "/api/managed-environments/ssh-config") == (
                404,
                {"code": "not_found", "error": "not found"},
            )
    assert not (tmp_path / "ssh").exists()


def test_the_ssh_session_is_own_running_only_and_never_logged(lane, svc, caplog):
    routes, client, _ = lane
    caplog.set_level(logging.DEBUG)
    sid = _own(client, svc)
    call(routes, "GET", "/api/managed-environments/list")
    status, body = call(routes, "POST", f"/api/managed-environments/{sid}/ssh-session")
    assert status == 200 and body["region"] == "ex-test-1" and body["tokenValue"] == "tv"
    assert "tv" not in caplog.text and "wss://x" not in caplog.text
    # a List that reads it STOPPED: refused with no call
    svc.sandboxes[sid]["status"] = "STOPPED"
    call(routes, "GET", "/api/managed-environments/list")
    asked = svc.ops().count("StartSshSession")
    status, body = call(routes, "POST", f"/api/managed-environments/{sid}/ssh-session")
    assert status == 409 and body["code"] == "not_ready"
    assert svc.ops().count("StartSshSession") == asked
    # Resume, then Connect with no List between: the mark is read again, not trusted
    assert call(routes, "POST", f"/api/managed-environments/{sid}/resume")[0] == 200
    svc.sandboxes[sid]["status"] = "RUNNING"
    assert call(routes, "POST", f"/api/managed-environments/{sid}/ssh-session")[0] == 200
    # a stop marks it; a session asked while it still reads STOPPED is refused after one read
    call(routes, "POST", f"/api/managed-environments/{sid}/stop")
    svc.sandboxes[sid]["status"] = "STOPPED"
    assert call(routes, "POST", f"/api/managed-environments/{sid}/ssh-session")[0] == 409
    svc.sandboxes[sid]["status"] = "RUNNING"
    assert call(routes, "POST", f"/api/managed-environments/{sid}/ssh-session")[0] == 200


def test_a_signed_out_lane_says_so(lane, svc):
    routes, client, _ = lane
    client._credential = lambda: Credential("")
    status, body = call(routes, "GET", "/api/managed-environments/list")
    assert status == 409 and body["code"] == "not_signed_in"
