"""The managed-environment lane's generic cloud routes are the machine owner's alone.

A Shared Crew teammate passes the dashboard owner gate (``_guard``). On the
``kiro_managed_env`` lane the cloud routes then ask the same host-owner check as the
lane's own routes and refuse the teammate with 403 ``owner_only``. The built-in EC2
lane keeps the owner gate alone.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_cloud_handlers import _body, _posix_host, _req, _state  # noqa: F401
from test_managed_env_engine import TAG, _launch, svc, world  # noqa: F401

from kiro_crew.dashboard import handlers_cloud as hc
from kiro_crew.managed_env import PROVISIONER_ID


@pytest.fixture
def teammate(monkeypatch):
    asked: list[str] = []

    def host_owner(request, operation):
        asked.append(operation)
        return False

    monkeypatch.setattr("kiro_crew.managed_env.routes.host_owner", host_owner)
    return asked


@pytest.mark.asyncio
async def test_a_teammate_is_refused_launch_and_delete_on_the_lane_but_not_on_ec2(
    tmp_path, monkeypatch, teammate
):
    lane = type("P", (), {"id": PROVISIONER_ID, "kind": PROVISIONER_ID, "posix_only": False})()
    monkeypatch.setattr(hc, "_provisioners", lambda: [lane])
    monkeypatch.setattr(hc, "_engine", lambda *a, **k: pytest.fail("no engine for a teammate"))
    state = _state(tmp_path)
    body = {"provider_id": PROVISIONER_ID, "profile": "crew-a", "region": "", "size_key": ""}
    resp = await hc.api_cloud_launch_create(
        _req("POST", "/api/cloud/launch", state=state, body=body)
    )
    assert (resp.status, _body(resp)["code"]) == (403, "owner_only")
    assert list(state.cloud_launch_store.root.glob("*.json")) == []  # no job written

    job = state.cloud_launch_store.create(
        profile="crew-a", region="", size_key="", provider_id=PROVISIONER_ID
    )
    job.tag = "kc-0a1b2c"
    state.cloud_launch_store.save(job)
    resp = await hc.api_cloud_destroy(
        _req("DELETE", "/api/cloud/kc-0a1b2c", state=state, match_info={"tag": "kc-0a1b2c"})
    )
    assert (resp.status, _body(resp)["code"]) == (403, "owner_only")

    from kiro_crew.dashboard.handlers_cloud_signin_skip import api_cloud_launch_signin_skip

    monkeypatch.setattr(
        "kiro_crew.dashboard.handlers_cloud_signin_skip.request_skip",
        lambda *a: pytest.fail("a teammate's skip reached the launch"),
    )
    resp = await api_cloud_launch_signin_skip(
        _req(
            "POST",
            f"/api/cloud/launch/{job.id}/signin/skip",
            state=state,
            match_info={"id": job.id},
        )
    )
    assert (resp.status, _body(resp)["code"]) == (403, "owner_only")
    assert teammate == ["cloud_launch_create", "cloud_destroy", "cloud_launch_signin_skip"]

    # EC2 is unchanged: the same teammate's delete goes on to the EC2 path, and the
    # host-owner check is never asked.
    teammate.clear()
    reached: list[str] = []
    monkeypatch.setattr(hc.ec2, "destroy", lambda *a, **k: reached.append("ec2.destroy") or {})
    monkeypatch.setattr(hc.ec2, "describe", lambda *a, **k: {"instance_id": "i-0abc"})
    monkeypatch.setattr(hc, "_start_teardown_watch", lambda *a, **k: None)
    ec2 = state.cloud_launch_store.create(profile="dev", region="us-east-1", size_key="balanced")
    ec2.tag = "kc-ec2aaa"
    state.cloud_launch_store.save(ec2)
    resp = await hc.api_cloud_destroy(
        _req("DELETE", "/api/cloud/kc-ec2aaa", state=state, match_info={"tag": "kc-ec2aaa"})
    )
    assert resp.status == 200, _body(resp)
    assert reached == ["ec2.destroy"]
    assert teammate == []


@pytest.fixture
def registered_chain(tmp_path, monkeypatch):
    from kiro_crew.instances.registry import InstancesRegistry
    from kiro_crew.testing.ids import UNALLOCATABLE_PID

    state = _state(tmp_path)
    reg = InstancesRegistry()
    reg.add(name="parent", ssh_host="parent.example")
    reg.add(
        name="child",
        ssh_host="child.example",
        via_instance_id="parent",
        via_remote_id="child",
        via_remote_port=5476,
    )
    for row in reg.list():
        reg.update(
            row.id,
            was_connected=True,
            forwarder_pid=UNALLOCATABLE_PID,
            forwarder_start="recorded",
        )
    state.instances_registry = reg
    connected = {"parent", "child"}
    events = []

    async def disconnect(cid, *, keep_intent):
        assert keep_intent is True
        assert {row.id for row in reg.list()} == {"parent", "child"}
        events.append(cid)
        connected.remove(cid)

    state.instances_manager = SimpleNamespace(disconnect=disconnect)

    def teardown(**kwargs):
        events.append("teardown")
        reg.remove_cascade("parent")
        return True

    state.cloud_launch_engine = SimpleNamespace(teardown=Mock(side_effect=teardown))
    unregister = Mock(wraps=hc.connect_mod.unregister_instance)
    monkeypatch.setattr(hc.connect_mod, "unregister_instance", unregister)
    monkeypatch.setattr("kiro_crew.managed_env.routes.host_owner", lambda *args: True)
    return state, reg, connected, events, unregister


@pytest.mark.asyncio
@pytest.mark.parametrize("match", ["ssm_target", "ssh_host", "id"])
async def test_generic_destroy_disconnects_child_then_parent_before_teardown(
    registered_chain, match
):
    lane = PROVISIONER_ID
    state, reg, connected, events, unregister = registered_chain
    registered = {
        "ssm_target": "mi-0abc123456789def0",
        "ssh_host": "parent.example",
        "id": "parent",
    }[match]
    if match == "ssm_target":
        reg.update("parent", connection_method="ssm", ssm_target=registered)
    job = state.cloud_launch_store.create(
        profile="dev", region="us-east-1", size_key="", provider_id=lane
    )
    job.tag = "kc-chain"
    job.instance_id = registered
    state.cloud_launch_store.save(job)

    resp = await hc.api_cloud_destroy(
        _req("DELETE", "/api/cloud/kc-chain", state=state, match_info={"tag": "kc-chain"})
    )

    assert resp.status == 200, _body(resp)
    assert events == ["child", "parent", "teardown"]
    assert connected == set()
    assert reg.list() == []
    state.cloud_launch_engine.teardown.assert_called_once_with(
        tag="kc-chain", profile="", region=""
    )
    unregister.assert_called_once_with(registered)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_id", ["child", "parent"])
@pytest.mark.parametrize("failure", [RuntimeError, TimeoutError])
async def test_generic_destroy_retains_rows_when_disconnect_raises(
    registered_chain, monkeypatch, failed_id, failure
):
    lane = PROVISIONER_ID
    state, reg, connected, events, unregister = registered_chain
    before = reg.list()
    audited = Mock()
    monkeypatch.setattr(hc, "_audit", audited)

    async def disconnect(cid, *, keep_intent):
        assert keep_intent is True
        assert reg.list() == before
        events.append(cid)
        if cid == failed_id:
            raise failure("stop failed")
        connected.remove(cid)

    state.instances_manager.disconnect = disconnect
    job = state.cloud_launch_store.create(
        profile="dev", region="us-east-1", size_key="", provider_id=lane
    )
    job.tag = "kc-chain"
    job.instance_id = "parent.example"
    state.cloud_launch_store.save(job)

    resp = await hc.api_cloud_destroy(
        _req("DELETE", "/api/cloud/kc-chain", state=state, match_info={"tag": "kc-chain"})
    )

    assert resp.status == 409, _body(resp)
    assert _body(resp)["code"] == "remove_teardown_failed"
    assert "Nothing was deleted." in _body(resp)["error"]
    assert events == (["child"] if failed_id == "child" else ["child", "parent"])
    assert failed_id in connected
    assert reg.list() == before
    state.cloud_launch_engine.teardown.assert_not_called()
    unregister.assert_not_called()
    audited.assert_called_once_with(
        "destroy", "denied", request_id="kc-chain", error="teardown_failed"
    )


@pytest.mark.asyncio
async def test_generic_destroy_of_an_accepted_delete_answers_ok_and_unregisters(
    registered_chain, world, svc  # noqa: F811
):
    """The first Delete is accepted while the List still shows the environment DELETING."""
    state, reg, connected, events, unregister = registered_chain
    engine, *_ = world
    svc.answer("GetSandbox", 200, {"execEnvironmentId": "env-001", "status": "RUNNING"})
    host = _launch(engine)
    deleting = {"sandboxes": [{"execEnvironmentId": "env-001", "status": "DELETING"}]}
    svc.answer("ListSandboxes", 200, deleting)
    svc.answer("ListSandboxes", 200, deleting)
    state.cloud_launch_engine = engine
    job = state.cloud_launch_store.create(
        profile="crew-a", region="", size_key="", provider_id=PROVISIONER_ID
    )
    job.tag = TAG
    job.instance_id = host
    state.cloud_launch_store.save(job)

    resp = await hc.api_cloud_destroy(
        _req("DELETE", f"/api/cloud/{TAG}", state=state, match_info={"tag": TAG})
    )

    assert resp.status == 200, _body(resp)
    assert (_body(resp)["ok"], _body(resp)["cleanup"]) == (True, "done")
    unregister.assert_called_once_with(host)
    assert svc.ops().count("DeleteSandbox") == 1


@pytest.mark.asyncio
async def test_generic_destroy_of_another_lane_keeps_main_behaviour(registered_chain):
    """Only the managed-environment lane disconnects first; other lanes are unchanged."""
    state, reg, connected, events, unregister = registered_chain
    job = state.cloud_launch_store.create(
        profile="dev", region="us-east-1", size_key="", provider_id="microvm"
    )
    job.tag = "kc-chain"
    job.instance_id = "parent.example"
    state.cloud_launch_store.save(job)

    resp = await hc.api_cloud_destroy(
        _req("DELETE", "/api/cloud/kc-chain", state=state, match_info={"tag": "kc-chain"})
    )

    assert resp.status == 200, _body(resp)
    assert events == ["teardown"]
    state.cloud_launch_engine.teardown.assert_called_once()
