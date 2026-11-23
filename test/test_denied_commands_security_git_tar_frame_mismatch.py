"""The git/tar-to-self floor fails closed when its two readings of a line (the
case it was written and lowered) disagree on frame count, instead of reading on
with the written-case option words dropped.

The regression rows below pin the fail-closed behaviour on the known
disagree shapes. The fuzz pass then asserts the safety OUTCOME across thousands
of random here-document / quote / case / separator lines wrapped around a
transport to this host: none escapes the floor.
"""

from __future__ import annotations

import random

import pytest
from denied_commands_security_helpers import (  # noqa: F401
    _denied_by,
    _own_address_probe_stays_local,
)

from kiro_crew.security import is_denied  # noqa: E402

_RULE = "sandbox-escape-git-tar-self"
_S = "local" + "host"


class TestFrameMismatchFailsClosed:
    # The quoted-separator + mixed-case here-document-tag shape that made the two
    # passes count a different number of frames. The floor now refuses a git/tar
    # transport to this host on such a line instead of dropping the written-case
    # option words. A remote host on the same shape is refused too -- an accepted,
    # rare, fail-closed over-refusal of a line whose two readings disagree.
    @pytest.mark.parametrize(
        ("cmd", "rule"),
        [
            ('echo "a;b"; sudo -H git clone ssh://' + _S + "/r; cat <<EOF\neof", _RULE),
            ('echo "a;b"; sudo -H tar -xf ' + _S + ":/x; cat <<EOF\neof", _RULE),
            ('echo "a;b"; sudo -H git clone ssh://github.com/r; cat <<EOF\neof', _RULE),
        ],
        ids=["self-git", "self-tar", "remote-fail-closed"],
    )
    def test_a_disagreeing_line_is_refused(self, cmd, rule):
        assert _denied_by(cmd) == rule, cmd

    # Two payloads that differ only in case are one frame lowered but two as
    # written. That is not a disagreement: a local command is still allowed.
    @pytest.mark.parametrize(
        "cmd",
        [
            "bash -c 'git log --format=%H' && bash -c 'git log --format=%h'",
            "sh -c 'git status -S' ; sh -c 'git status -s'",
            "bash -c 'tar -cf A.tar x' && bash -c 'tar -cf a.tar x'",
            "bash -c 'sudo -h git clone ssh://github.com/r' && "
            "bash -c 'sudo -H git clone ssh://github.com/r'",
        ],
        ids=["git-log", "git-status", "tar-local", "remote"],
    )
    def test_case_only_duplicate_payloads_are_allowed(self, cmd):
        assert is_denied(cmd) is None, cmd

    # Each written spelling of a case-only duplicate is read: ``sudo -H`` runs the
    # git/tar word, so the pair dials this host whichever spelling comes first.
    @pytest.mark.parametrize(
        "cmd",
        [
            f"bash -c 'sudo -h git clone {_S}:/x' && bash -c 'sudo -H git clone {_S}:/x'",
            f"bash -c 'sudo -H git clone {_S}:/x' && bash -c 'sudo -h git clone {_S}:/x'",
            f"bash -c 'sudo -h tar -xf {_S}:/x' ; bash -c 'sudo -H tar -xf {_S}:/x'",
        ],
        ids=["git-h-first", "git-H-first", "tar"],
    )
    def test_every_spelling_of_a_case_only_duplicate_is_read(self, cmd):
        assert _denied_by(cmd) == _RULE, cmd

    # The same disagreeing shape on a line that runs no git/tar and carries no
    # self-dial signal is ordinary shell, so it is allowed: the refusal needs a
    # self-dial signal, as the floor's other fail-closed reads do.
    @pytest.mark.parametrize(
        "cmd",
        [
            'echo "a;b"; npm start; cat <<EOF\neof',
            'echo "a;b"; npm run legit; cat <<EOF\neof',
        ],
        ids=["npm-start", "git-substring"],
    )
    def test_a_disagreeing_line_with_no_self_dial_signal_is_allowed(self, cmd):
        assert is_denied(cmd) is None, cmd

    def test_fuzz_no_self_transport_escapes_a_heredoc_shape(self):
        # The safety outcome of failing closed: a git or tar transport to this
        # host is refused however a here-document, stray quote, case or separator
        # is wrapped around it. ``sudo -H`` keeps the verb (it takes no value);
        # ``sudo -h`` is excluded because it consumes the next word, so the line
        # runs no git/tar at all.
        rng = random.Random(0x5EED)  # fixed seed: deterministic
        heredocs = [
            "",
            "; cat <<EOF\neof",
            "; cat <<EOF\nEOF",
            " <<< word",
            "; cat <<'EOF'\n'\nEOF",
            "; cat <<A\nit's\nA",
            "\ncat <<EOF\nX\neof",
            "; x=$((1<<2))",
            " <<e\n'\nE",
        ]
        before = ["", 'echo "a;b"; ', "echo 'q'; ", "true && ", "x=1; "]
        selfhosts = ["ssh://" + _S + "/r", _S + ":/x", "127.0.0.1:/x"]
        verbs = ["git clone ", "tar -xf "]
        opts = ["", "sudo ", "sudo -H ", "env d=1 "]

        def mix_case(s: str) -> str:
            return "".join(c.upper() if rng.random() < 0.3 else c for c in s)

        for _ in range(4000):
            cmd = mix_case(
                rng.choice(before)
                + rng.choice(opts)
                + rng.choice(verbs)
                + rng.choice(selfhosts)
                + rng.choice(heredocs)
            )
            assert is_denied(cmd) is not None, cmd

    def test_fuzz_remote_plain_transport_stays_allowed(self):
        # Guard the other side: a plain transport to a remote host with no
        # disagreeing here-document shape is still allowed, so the fail-closed
        # rule did not swallow ordinary remote use.
        rng = random.Random(0x1234)  # fixed seed: deterministic
        remotes = ["ssh://github.com/r", "github.com:/x", "git.example.org:/x"]
        verbs = ["git clone ", "tar -xf "]
        opts = ["", "sudo -H "]
        for _ in range(500):
            cmd = opts[rng.randrange(len(opts))] + rng.choice(verbs) + rng.choice(remotes)
            assert is_denied(cmd) is None, cmd
