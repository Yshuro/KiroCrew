"""The managed-environment lane's generic cloud routes are the machine owner's alone.

A Shared Crew teammate passes the dashboard owner gate (``_guard``). On the
``kiro_managed_env`` lane the cloud routes then ask the same host-owner check as the
lane's own routes and refuse the teammate with 403 ``owner_only``. The built-in EC2
lane keeps the owner gate alone.
"""

from __future__ import annotations

import pytest
from test_cloud_handlers import _body, _posix_host, _req, _state  # noqa: F401

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
