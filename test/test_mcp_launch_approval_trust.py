"""The "Always trust changes to this server" choice, per server, set only in the dashboard.

A server approved with the box checked keeps a changed launch shared at the next
gateway start; one approved without it asks again, as before. Only the approve
press with a displayed launch can set it: a card (``expected_entry``), a press
with no launch, an agent-declared key and a rewrite pass set nothing. Stopping
sharing, and a complete full pass that does not find the server, drop it; a
pass served from cache keeps it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp_launch_approval_helpers import (  # noqa: F401 -- the fixture is requested by name
    _APPROVED_CMD,
    _approval_module,
    _declare,
    _next_boot_rewrite,
    _stub_request,
    _targets_running,
    toggle_home_fixture,
)

#: Opts this module out of the conftest fixture that approves every gatewayd launch.
ENFORCE_LAUNCH_APPROVAL = True


async def _press(body_extra: dict, *, with_launch: bool = True):
    from kiro_crew.dashboard.handlers import mcp as mcp_mod

    body = {"name": "srv", "stub": True, **body_extra}
    if with_launch:
        launches = await asyncio.to_thread(
            mcp_mod.launch_resolve.resolve_launches, ["srv"], refresh_specs=False
        )
        body["expected_launch"] = mcp_mod.display_launches("srv", launches["srv"])[
            "expected_launch"
        ]
    resp = await mcp_mod.api_mcp_gateway_set_stub(_stub_request(body))
    return resp.status, json.loads(resp.body)


def _trusted() -> set[str]:
    return _approval_module().load_approvals().trusted


@pytest.mark.asyncio
async def test_a_trusted_server_keeps_a_changed_launch_shared(toggle_home, tmp_path):
    _declare(toggle_home, "agent spec", _APPROVED_CMD, [])
    status, _ = await _press({"trust_changes": True})
    assert status == 200
    assert _trusted() == {"SRV"}

    _declare(toggle_home, "agent spec", _APPROVED_CMD, ["-V"])
    target_env, approvals = _next_boot_rewrite(tmp_path, toggle_home)

    assert approvals.refused == {}
    assert _targets_running(target_env, _APPROVED_CMD)


@pytest.mark.asyncio
async def test_an_untrusted_server_asks_again_after_a_change(toggle_home, tmp_path):
    mod = _approval_module()
    _declare(toggle_home, "agent spec", _APPROVED_CMD, [])
    status, _ = await _press({})
    assert status == 200
    assert _trusted() == set()

    _declare(toggle_home, "agent spec", _APPROVED_CMD, ["-V"])
    target_env, approvals = _next_boot_rewrite(tmp_path, toggle_home)

    assert approvals.refused.get("SRV") == mod.REFUSED_CHANGED
    assert not _targets_running(target_env, _APPROVED_CMD)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra, with_launch",
    [
        ({"trust_changes": True, "expected_entry": {"command": _APPROVED_CMD}}, True),
        ({"trust_changes": True}, False),
        ({"trust_changes": "yes"}, True),
    ],
)
async def test_only_the_approve_press_can_set_trust(toggle_home, extra, with_launch):
    _declare(toggle_home, "agent spec", _APPROVED_CMD, [])
    status, body = await _press(extra, with_launch=with_launch)

    assert status == 400
    assert body["code"] == "trust_changes_invalid"
    assert _trusted() == set()


@pytest.mark.asyncio
async def test_an_agent_written_trust_key_sets_nothing(toggle_home, tmp_path):
    """An agent can write the server's entry, not the approval store: a
    ``trust_changes`` key in a spec, and a rewrite pass over it, trust nothing."""
    mod = _approval_module()
    entry = {"command": _APPROVED_CMD, "args": [], "trust_changes": True}
    (toggle_home / "a.json").write_text(
        json.dumps({"name": "a", "tools": ["*"], "mcpServers": {"srv": entry}})
    )
    status, _ = await _press({})
    assert status == 200

    _target_env, approvals = _next_boot_rewrite(tmp_path, toggle_home)
    mod.save_pass(approvals)

    assert _trusted() == set()


@pytest.mark.asyncio
async def test_stopping_sharing_drops_trust(toggle_home):
    from kiro_crew.dashboard.handlers import mcp as mcp_mod

    _declare(toggle_home, "agent spec", _APPROVED_CMD, [])
    await _press({"trust_changes": True})
    assert _trusted() == {"SRV"}

    resp = await mcp_mod.api_mcp_gateway_set_stub(_stub_request({"name": "srv", "stub": False}))

    assert resp.status == 200
    assert _trusted() == set()


@pytest.mark.asyncio
async def test_a_server_no_longer_declared_loses_trust(toggle_home, tmp_path):
    """A removed server keeps its name on the share list; one added again under
    that name must not inherit the choice."""
    mod = _approval_module()
    _declare(toggle_home, "agent spec", _APPROVED_CMD, [])
    await _press({"trust_changes": True})
    (toggle_home / "a.json").write_text(json.dumps({"name": "a", "tools": ["*"], "mcpServers": {}}))

    _target_env, approvals = _next_boot_rewrite(tmp_path, toggle_home)
    mod.save_pass(approvals)

    assert _trusted() == set()


def test_the_store_reads_trust_only_as_true(tmp_path: Path):
    mod = _approval_module()
    store = tmp_path / "approvals.json"
    record = {"name": "srv", "fingerprints": ["a:b"], "pairs": ["a:b"]}
    for value, trusted in ((True, {"SRV"}), ("true", set()), (1, set())):
        doc = {"version": 1, "servers": {"SRV": {**record, "trust_changes": value}}, "refused": {}}
        store.write_text(json.dumps(doc))
        assert mod.load_approvals(store).trusted == trusted


@pytest.mark.asyncio
async def test_the_sharing_row_can_stop_trusting_changes(toggle_home):
    """``trust_changes: false`` alone takes the choice off and keeps the share."""
    from kiro_crew.dashboard.handlers import mcp as mcp_mod

    _declare(toggle_home, "agent spec", _APPROVED_CMD, [])
    await _press({"trust_changes": True})
    approved = _approval_module().load_approvals().approved["SRV"]

    resp = await mcp_mod.api_mcp_gateway_set_stub(
        _stub_request({"name": "srv", "trust_changes": False})
    )

    assert resp.status == 200
    assert _trusted() == set()
    assert _approval_module().load_approvals().approved["SRV"] == approved


def test_a_pass_served_from_cache_keeps_trust(tmp_path: Path):
    """A restart with nothing changed reuses the cached rewrite: that pass admits
    nothing, so it cannot tell that a server is gone."""
    mod = _approval_module()
    store = tmp_path / "approvals.json"
    record = {"name": "srv", "fingerprints": ["a:b"], "pairs": ["a:b"], "trust_changes": True}
    store.write_text(json.dumps({"version": 1, "servers": {"SRV": record}, "refused": {}}))
    cached = mod.LaunchApprovals()
    assert not cached.full_pass

    mod.save_pass(cached, path=store)

    assert mod.load_approvals(store).trusted == {"SRV"}


def _capture_sel(monkeypatch) -> list[dict]:
    from kiro_crew.dashboard.handlers import mcp as mcp_mod

    events: list[dict] = []
    monkeypatch.setattr(
        mcp_mod,
        "sel",
        lambda: type("_Sel", (), {"log_api_access": lambda _self, **kw: events.append(kw)})(),
    )
    return events


@pytest.mark.asyncio
async def test_granting_and_taking_off_trust_are_audited(toggle_home, monkeypatch):
    from kiro_crew.dashboard.handlers import mcp as mcp_mod

    events = _capture_sel(monkeypatch)
    _declare(toggle_home, "agent spec", _APPROVED_CMD, [])
    await _press({"trust_changes": True})
    await mcp_mod.api_mcp_gateway_set_stub(_stub_request({"name": "srv", "trust_changes": False}))

    ok = [e for e in events if e.get("outcome") == "ok"]
    assert any(
        e["operation"] == "mcp_gateway_set_stub" and "trust_changes=True" in e["resources"]
        for e in ok
    )
    assert any(
        e["operation"] == "mcp_gateway_trust_changes" and "trust_changes=False" in e["resources"]
        for e in ok
    )


@pytest.mark.asyncio
async def test_a_still_declared_server_keeps_trust_when_its_launch_is_skipped(
    toggle_home, tmp_path
):
    """A command the gateway cannot resolve for one pass (an upgrade moving the
    binary) skips the launch but keeps the server declared, so its trust stays."""
    mod = _approval_module()
    _declare(toggle_home, "agent spec", _APPROVED_CMD, [])
    await _press({"trust_changes": True})
    _declare(toggle_home, "agent spec", "no-such-mcp-binary-for-this-test", [])

    _target_env, approvals = _next_boot_rewrite(tmp_path, toggle_home)
    mod.save_pass(approvals)

    assert _trusted() == {"SRV"}


def test_a_server_declared_only_in_the_global_settings_counts_as_declared(tmp_path: Path):
    """A server that an agent inherits from the global MCP settings is still
    declared when that agent's own ``mcpServers`` is empty, or leaves it out."""
    from kiro_crew.mcp_gateway import rewriter_spec

    mod = _approval_module()
    approvals = mod.LaunchApprovals()
    rewriter_spec._rewrite_single_spec(
        {"name": "a", "tools": [], "mcpServers": {}},
        stubs_dir=tmp_path / "stubs",
        socket_path=tmp_path / "gw.sock",
        work_dir=tmp_path / "work",
        sandbox_mode="off",
        approval_mode="normal",
        stub_servers=frozenset({"srv"}),
        inject_servers={"srv": {"command": _APPROVED_CMD, "args": []}},
        approvals=approvals,
    )

    assert "SRV" in approvals.declared


def test_withdrawn_trust_drops_the_launch_pair_too(tmp_path: Path):
    """When trust is taken off while a pass runs, the save writes neither the
    trusted fingerprint nor the launch pair it admitted."""
    mod = _approval_module()
    store = tmp_path / "approvals.json"
    record = {"name": "srv", "fingerprints": ["c:old"], "pairs": ["c:old"]}
    store.write_text(json.dumps({"version": 1, "servers": {"SRV": record}, "refused": {}}))
    approvals = mod.LaunchApprovals()
    approvals.names["SRV"] = "srv"
    # A second launch of the same command, admitted another way, survives the save.
    approvals.captured["SRV"] = {"c:new", "c:kept"}
    approvals.captured_pairs["SRV"] = {"c:new", "c:kept"}
    approvals.trust_admitted["SRV"] = {"c:new": "c:new"}

    mod.save_pass(approvals, path=store)

    stored = mod.load_approvals(store)
    assert "c:new" not in stored.approved.get("SRV", set())
    assert "c:new" not in stored.approved_pairs.get("SRV", set())
    assert "c:kept" in stored.approved_pairs["SRV"]
