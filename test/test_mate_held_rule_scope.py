"""A held Bot limit covers the calls it names, and no others (``mate_held_scope``).

A limit the upgrade could not carry is held: its calls ask a person even under
Trust. Pins that a held hook on a tool Crew cannot map (``use_aws``) holds that
tool's calls only, and that a held path rule (a deny on ``~/.aws/credentials``, a
``deniedPaths`` setting) holds only the reads or writes that touch that path. A call
whose target cannot be told is still held: the narrowing never guesses a call out.
Each case has its control.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import kiro_crew.config.paths as paths_mod
from kiro_crew import mate_held_rules
from kiro_crew.acp.types import EVENT_PERMISSION_REQUEST, AcpEvent

BASE = {
    "name": "kirocrew-mate-worker",
    "prompt": "worker base prompt",
    "tools": ["fs_read", "@kirocrew-core"],
    "allowedTools": ["fs_read"],
    "mcpServers": {"kirocrew-core": {"command": "kc", "args": ["core"]}},
}
_FILE = {"name": "zxc", "mcpServers": {}, "tools": [], "allowedTools": []}


@pytest.fixture
def ledger(monkeypatch, tmp_path) -> Path:
    mates = tmp_path / "home" / "mates" / "agents"
    mates.mkdir(parents=True)
    monkeypatch.setattr(paths_mod, "kiro_home", lambda: tmp_path / "home")
    return mates.parent / "migrated.json"


def _hold(ledger: Path, *rules: dict) -> None:
    ledger.write_text(
        json.dumps({"held_rules": {"zxc": {"member_id": "", "rules": list(rules)}}}),
        encoding="utf-8",
    )


def _call(kind: str = "", **fields) -> AcpEvent:
    return AcpEvent(kind=EVENT_PERMISSION_REQUEST, title="a call", tool_kind=kind, **fields)


def _held(event: AcpEvent) -> list[str]:
    return mate_held_rules.held_for(event, agent="zxc")


# ── 1. A hook on a tool Crew cannot map holds that tool alone ────────────────


def test_a_held_hook_on_an_unmapped_tool_is_recorded_for_that_tool():
    hooks = {"preToolUse": [{"matcher": "use_aws", "command": "check-aws"}]}

    _out, held = mate_held_rules.split_policy(False, BASE, {"hooks": hooks}, _FILE)

    assert [h["capability"] for h in held] == ["tool:use_aws"]


@pytest.mark.parametrize("matcher", ["use_*", "*", "", "a b"])
def test_a_matcher_that_names_no_single_tool_still_covers_every_call(matcher):
    hooks = {"preToolUse": [{"matcher": matcher, "command": "x"}]}

    _out, held = mate_held_rules.split_policy(False, BASE, {"hooks": hooks}, _FILE)

    assert [h["capability"] for h in held] == [mate_held_rules.ANY]


def test_a_tool_limit_holds_that_tools_calls_and_nothing_else(ledger):
    _hold(ledger, {"text": "its preToolUse hook on use_aws", "capability": "tool:use_aws"})

    assert _held(_call("other", harness_tool_id="use_aws")) == ["its preToolUse hook on use_aws"]
    assert _held(_call("execute", harness_tool_id="run_command", is_shell=True)) == []
    assert _held(_call("read", harness_tool_id="read_file")) == []
    assert _held(_call("other", tool_name="ReadInternalWebsites", mcp_server_name="b")) == []


def test_a_call_whose_tool_cannot_be_told_is_still_held_by_a_tool_limit(ledger):
    _hold(ledger, {"text": "its preToolUse hook on use_aws", "capability": "tool:use_aws"})

    assert _held(_call()) == ["its preToolUse hook on use_aws"]


def test_a_tool_limit_strips_only_that_tools_pre_approval():
    spec = {"allowedTools": ["use_aws", "fs_read", "execute_bash"]}

    out = mate_held_rules._strip_caps(spec, {"tool:use_aws"})

    assert out["allowedTools"] == ["fs_read", "execute_bash"]


# ── 2. A path rule holds only the calls that touch its path ──────────────────


_CREDS = "~/.aws/credentials"


def test_a_held_deny_on_a_path_is_recorded_with_its_paths():
    rule = {"capability": "fs_read", "effect": "deny", "match": [_CREDS]}

    _out, held = mate_held_rules.split_policy(
        False, BASE, {"permissions": {"rules": [rule]}}, _FILE
    )

    assert [(h["capability"], h.get("paths")) for h in held] == [("fs_read", [_CREDS])]


def test_a_held_denied_paths_setting_is_recorded_with_its_paths():
    settings = {"fs_write": {"deniedPaths": [_CREDS], "allowedPaths": ["~/work"]}}

    _out, held = mate_held_rules.split_policy(False, BASE, {"toolsSettings": settings}, _FILE)

    assert [(h["capability"], h.get("paths")) for h in held] == [("fs_write", [_CREDS])]


def test_a_setting_that_limits_more_than_paths_still_covers_every_such_call():
    settings = {"fs_read": {"deniedPaths": [_CREDS], "maxBytes": 10}}

    _out, held = mate_held_rules.split_policy(False, BASE, {"toolsSettings": settings}, _FILE)

    assert [(h["capability"], h.get("paths")) for h in held] == [("fs_read", None)]


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    home = tmp_path / "userhome"
    (home / ".aws").mkdir(parents=True)
    (home / ".aws" / "credentials").write_text("x", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    # ``~`` reads USERPROFILE on Windows.
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _read(path: str, **fields) -> AcpEvent:
    return _call("read", harness_tool_id="read_file", raw_tool_params={"path": path}, **fields)


def test_a_path_limit_holds_a_read_of_that_path_and_not_of_another(ledger, home, tmp_path):
    text = "its permissions rule to deny fs_read (~/.aws/credentials)"
    _hold(ledger, {"text": text, "capability": "fs_read", "paths": [_CREDS]})
    other = tmp_path / "notes.txt"
    other.write_text("x", encoding="utf-8")

    assert _held(_read(str(home / ".aws" / "credentials"))) == [text]
    assert _held(_read("~/.aws/credentials")) == [text]
    assert _held(_read(str(other))) == []
    # The limit names reads only: a write of the same path is not under it.
    write = _call("edit", harness_tool_id="fs_write", raw_tool_params={"path": _CREDS})
    assert _held(write) == []


def test_a_path_limit_holds_a_read_of_a_folder_that_holds_its_path(ledger, home):
    _hold(ledger, {"text": "t", "capability": "fs_read", "paths": [_CREDS]})

    assert _held(_read(str(home / ".aws"))) == ["t"]
    assert _held(_read(str(home))) == ["t"]


def test_a_path_limit_follows_a_link_to_its_path(ledger, home, tmp_path):
    _hold(ledger, {"text": "t", "capability": "fs_read", "paths": [_CREDS]})
    link = tmp_path / "innocent.txt"
    os.symlink(home / ".aws" / "credentials", link)

    assert _held(_read(str(link))) == ["t"]


def test_a_relative_path_is_judged_in_the_calls_folder(ledger, home, tmp_path):
    _hold(ledger, {"text": "t", "capability": "fs_read", "paths": [_CREDS]})
    work = tmp_path / "work"
    work.mkdir()

    assert _held(_read("notes.txt", spec_write_cwd=str(work))) == []
    assert _held(_read(".aws/credentials", spec_write_cwd=str(home))) == ["t"]


@pytest.mark.parametrize(
    "params",
    [None, {}, {"path": "notes.txt"}, {"path": "$HOME/x"}],
    ids=["no-params", "no-path", "relative-no-folder", "variable"],
)
def test_a_read_whose_target_cannot_be_told_is_still_held(ledger, home, params):
    _hold(ledger, {"text": "t", "capability": "fs_read", "paths": [_CREDS]})

    event = _call("read", harness_tool_id="read_file", raw_tool_params=params)

    assert _held(event) == ["t"]


def test_a_glob_path_limit_holds_the_paths_it_matches(ledger, home, tmp_path):
    _hold(ledger, {"text": "t", "capability": "fs_read", "paths": ["**/.env"]})
    env = tmp_path / "proj" / ".env"
    env.parent.mkdir()
    env.write_text("x", encoding="utf-8")

    assert _held(_read(str(env))) == ["t"]
    assert _held(_read(str(tmp_path / "proj" / "main.py"))) == []


def test_a_relative_glob_with_a_folder_holds_that_folders_matches(ledger, home, tmp_path):
    _hold(ledger, {"text": "t", "capability": "fs_read", "paths": ["secrets/*.json"]})
    key = tmp_path / "proj" / "secrets" / "key.json"
    key.parent.mkdir(parents=True)
    key.write_text("x", encoding="utf-8")

    assert _held(_read(str(key))) == ["t"]
    assert _held(_read(str(tmp_path / "proj" / "key.json"))) == []


# ── Limits an earlier release recorded too widely are narrowed once ──────────


_HOOK_TEXT = "its preToolUse hook on use_aws"
_DENY_TEXT = "its permissions rule to deny fs_read (~/.aws/credentials)"
_OLD_AGENT = {
    "name": "old",
    "hooks": {"preToolUse": [{"matcher": "use_aws", "command": "check-aws"}]},
    "permissions": {
        "rules": [
            {"capability": "fs_read", "effect": "deny", "match": [_CREDS]},
            {"capability": "shell", "effect": "deny"},
        ]
    },
}


@pytest.fixture
def wide_record(ledger, monkeypatch):
    from kiro_crew import mate_config_migration, mate_mount_repair

    rules = [
        {"text": _HOOK_TEXT, "capability": mate_held_rules.ANY},
        {"text": _DENY_TEXT, "capability": "fs_read"},
        {"text": "its permissions rule to deny shell", "capability": "shell"},
    ]
    ledger.write_text(
        json.dumps(
            {
                "threads": {"zxc": {"from": "old"}},
                "held_rules": {"zxc": {"member_id": "m1", "rules": rules}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mate_config_migration, "_worker_base", lambda: dict(BASE))
    monkeypatch.setattr(mate_mount_repair, "_old_agent", lambda source: dict(_OLD_AGENT))
    return ledger


def _recorded(ledger: Path) -> list[dict]:
    return json.loads(ledger.read_text(encoding="utf-8"))["held_rules"]["zxc"]["rules"]


def test_a_wide_record_is_narrowed_to_what_each_limit_names(wide_record):
    from kiro_crew.mate_held_rescope import rescope_held_limits

    assert rescope_held_limits() == ["zxc"]

    assert _recorded(wide_record) == [
        {"text": _HOOK_TEXT, "capability": "tool:use_aws"},
        {"text": _DENY_TEXT, "capability": "fs_read", "paths": [_CREDS]},
        {"text": "its permissions rule to deny shell", "capability": "shell"},
    ]
    # Read once: a later start leaves the record alone.
    data = json.loads(wide_record.read_text(encoding="utf-8"))
    assert data["held_scope_rechecked"] == ["zxc"]
    assert rescope_held_limits() == []


def test_a_record_whose_old_agent_cannot_be_read_keeps_its_reach(wide_record, monkeypatch):
    from kiro_crew import mate_mount_repair
    from kiro_crew.mate_held_rescope import rescope_held_limits

    monkeypatch.setattr(mate_mount_repair, "_old_agent", lambda source: None)

    assert rescope_held_limits() == []
    assert _recorded(wide_record)[0]["capability"] == mate_held_rules.ANY
    # Not marked read, so the next start tries again.
    assert "held_scope_rechecked" not in json.loads(wide_record.read_text(encoding="utf-8"))


def test_the_narrowing_never_widens_a_recorded_limit():
    from kiro_crew.mate_held_rescope import narrowed

    scopes = {"t": {"text": "t", "capability": mate_held_rules.ANY}}
    rule = {"text": "t", "capability": "shell"}

    assert narrowed(rule, scopes) == rule
    assert narrowed(rule, {"t": None}) == rule
    assert narrowed(rule, {}) == rule


# ── 3. The card says which held limit made it ask ────────────────────────────


def test_the_card_notice_names_the_held_limits_and_where_they_are_listed():
    notice = mate_held_rules.card_notice([_HOOK_TEXT, _HOOK_TEXT, _DENY_TEXT])

    assert notice.count(_HOOK_TEXT) == 1
    assert _DENY_TEXT in notice
    assert "Profile tab" in notice


def test_the_card_notice_quotes_at_most_a_bounded_text():
    notice = mate_held_rules.card_notice(["x" * 5000])

    assert len(notice) < 600


@pytest.mark.asyncio
async def test_a_trusting_dashboard_chat_shows_the_held_limit_beside_its_card(tmp_path):
    from unittest.mock import AsyncMock, MagicMock, patch

    from chat_runner_coverage_helpers import (
        _complete,
        _drive,
        _permission,
        _runner_state,
        _set_stream,
        _slot,
    )

    from kiro_crew.dashboard import chat_runner

    state, client = _runner_state(tmp_path)
    slot = _slot()
    slot._trust = True
    _set_stream(client, [_permission(tool_input=json.dumps({"command": "ls"})), _complete()])
    from kiro_crew.testing.synthetic_credentials import github_classic

    token = github_classic()
    secret = f"its preToolUse hook on use_aws {token}"
    with (
        patch.object(mate_held_rules, "held_for", return_value=[secret]),
        patch.object(chat_runner, "sel", return_value=MagicMock()),
        # A card someone can still answer carries the notices; it then lapses.
        patch.object(chat_runner, "tool_approval_timeout_secs", return_value=0.05),
        patch.object(chat_runner, "_name_grant_refusal_off_loop", new=AsyncMock(return_value=None)),
        patch.object(chat_runner, "_name_grant_refusal_for", new=AsyncMock(return_value=None)),
    ):
        await _drive(state, slot)

    client.approve_tool.assert_not_awaited()
    notices = [m["content"] for m in slot.messages if m.get("role") == "system"]
    named = [n for n in notices if "its preToolUse hook on use_aws" in n]
    assert named and "Profile tab" in named[0]
    assert token not in named[0]
