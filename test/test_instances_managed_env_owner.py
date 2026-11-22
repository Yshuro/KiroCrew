"""A Kiro managed environment's Remote Crew row is the machine owner's alone.

A Shared Crew teammate passes the instances ``_guard``. On a row the managed-environment
lane wrote (or one dialling its ``crew-sbx-<id>`` alias, or chained through one) every
route that reaches the environment's tunnel or token, or removes the row, refuses the
teammate with 403 ``owner_only``, as the lane's own routes do. Other rows are unchanged.
"""

from __future__ import annotations

import asyncio

import pytest
from instances_helpers import _body, _enable, _FakeReq, _State

from kiro_crew.managed_env import PROVISIONER_ID

TEAMMATE = "bob@example.com"


@pytest.fixture
def reg(tmp_path, monkeypatch):
    from kiro_crew.instances.registry import InstancesRegistry

    _enable(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "kiro_crew.shared_crew.teammate_access.teammate_passes_owner_gate",
        lambda request: request.get("user") == TEAMMATE,
    )
    monkeypatch.setattr("kiro_crew.managed_env.settings.lane_enabled", lambda cfg=None: True)
    r = InstancesRegistry(path=tmp_path / "instances.json")
    r.add(name="sbx", ssh_host="crew-sbx-env-001", provisioner_id=PROVISIONER_ID, instance_id="sbx")
    r.add(name="ec2", ssh_host="ec2.example", instance_id="ec2")
    r.add(
        name="child",
        ssh_host="child.example",
        via_instance_id="sbx",
        via_remote_id="child",
        via_remote_port=5476,
        instance_id="child",
    )
    return r


def _handler(name):
    from kiro_crew.dashboard import handlers_crew_turn
    from kiro_crew.dashboard import handlers_instances as hi

    if name == "crew_turn":
        return handlers_crew_turn.api_crew_turn
    return getattr(hi, f"api_instances_{name}")


_ROUTES = [
    "connect",
    "remove",
    "update",
    "refresh_token",
    "embed_token",
    "disconnect",
    "restart",
    "update_remote",
    "send_session",
    "capabilities",
    "approval_state",
    "chat_slots",
    "proxy",
    "crew_turn",
]


def _call(reg, route, iid, user):
    match = {"id": iid, "path": "api/health"}
    req = _FakeReq(_State(reg), match=match, user=user, body={})
    return asyncio.run(_handler(route)(req))


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("iid", ["sbx", "child"])
def test_a_teammate_is_refused_on_a_managed_environment(reg, route, iid):
    before = reg.list()
    resp = _call(reg, route, iid, TEAMMATE)
    assert (resp.status, _body(resp)["code"]) == (403, "owner_only")
    assert reg.list() == before


@pytest.mark.parametrize("route", ["connect", "remove"])
def test_the_owner_still_passes_on_a_managed_environment(reg, route):
    resp = _call(reg, route, "sbx", "owner")
    if route == "connect":
        assert resp.status == 503  # reached the manager check: no manager in this state
    else:
        assert resp.status == 200 and _body(resp) == {"removed": "sbx"}


@pytest.mark.parametrize("route", ["connect", "remove"])
def test_another_provisioners_row_is_unchanged_for_a_teammate(reg, route):
    resp = _call(reg, route, "ec2", TEAMMATE)
    if route == "connect":
        assert resp.status == 503
    else:
        assert resp.status == 200 and _body(resp) == {"removed": "ec2"}


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "ssh_host": "crew-sbx-env-002"},
        {
            "name": "x",
            "ssh_host": "x.example",
            "via_instance_id": "sbx",
            "via_remote_id": "x",
            "via_remote_port": 5477,
        },
    ],
)
def test_a_teammate_cannot_add_a_row_that_reaches_a_managed_environment(reg, body):
    from kiro_crew.dashboard import handlers_instances as hi

    before = reg.list()
    resp = asyncio.run(hi.api_instances_add(_FakeReq(_State(reg), user=TEAMMATE, body=body)))
    assert (resp.status, _body(resp)["code"]) == (403, "owner_only")
    assert reg.list() == before
    resp = asyncio.run(hi.api_instances_add(_FakeReq(_State(reg), user="owner", body=body)))
    assert resp.status == 201, _body(resp)


def test_a_teammate_cannot_point_a_row_at_a_managed_environment(reg):
    from kiro_crew.dashboard import handlers_instances as hi

    body = {"ssh_host": "crew-sbx-env-002"}
    req = _FakeReq(_State(reg), match={"id": "ec2"}, user=TEAMMATE, body=body)
    resp = asyncio.run(hi.api_instances_update(req))
    assert (resp.status, _body(resp)["code"]) == (403, "owner_only")
    assert reg.get("ec2").ssh_host == "ec2.example"


def test_with_the_preview_off_a_teammate_is_answered_as_on_main(reg, monkeypatch):
    from kiro_crew.dashboard import handlers_instances as hi

    monkeypatch.setattr("kiro_crew.managed_env.settings.lane_enabled", lambda cfg=None: False)
    body = {"name": "alias", "ssh_host": "crew-sbx-env-003"}
    resp = asyncio.run(hi.api_instances_add(_FakeReq(_State(reg), user=TEAMMATE, body=body)))
    assert resp.status == 201, _body(resp)
    resp = _call(reg, "remove", "sbx", TEAMMATE)
    assert resp.status == 200, _body(resp)
