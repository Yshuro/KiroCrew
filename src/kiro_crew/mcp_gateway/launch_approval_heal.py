"""Launches that keep an approval while their stored digest differs.

Two repairs leave the launch the person approved byte for byte the same while
its digest moves, so :meth:`LaunchApprovals.admit` admits the new digest under
the approved one instead of asking again:

* the PATHEXT casing repair rewrites the extension case of a Windows command
  (:func:`casing_repair_of_approved`);
* launch approvals began folding the gateway's own part of ``PATH``
  (``hashing.hash_launch_env``), so a store written before that holds the
  literal digests of launches that have not changed (:func:`prefold_approval_of`).

Each returns the approved fingerprint and pair the new digest is admitted under;
``save_pass`` writes the new digest only while the store still approves that
source. Standard library only, like :mod:`kiro_crew.mcp_gateway.launch_approval`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Collection, Iterable, Mapping

from kiro_crew.mcp_gateway.hashing import hash_command, hash_declared_env

if TYPE_CHECKING:
    from kiro_crew.mcp_gateway.launch_approval import LaunchApprovals


def casing_repair_of_approved(
    approvals: LaunchApprovals,
    stem: str,
    fingerprint: str,
    derived_env_hash: str,
    launch: tuple[str, list[str]] | None,
) -> tuple[str, str] | None:
    """The approved fingerprint and pair *launch* repairs, or ``None``.

    The approved spelling differs from the live command only in the case of
    its extension, the repair maps it onto the live command, and its exact
    pair, same args and env, is approved with a stored display. The repair
    reads the spelling from the directory entry, so no agent chooses it.
    """
    if launch is None:
        return None
    from kiro_crew.env import saved_command_casing  # deferred: config.loader import path
    from kiro_crew.mcp_gateway.launch_approval import launch_pair

    command, args = str(launch[0]), [str(a) for a in launch[1]]
    root, ext = os.path.splitext(command)
    env_hash = fingerprint.partition(":")[2]
    for spelling in {root + ext.upper(), root + ext.lower()} - {command}:
        approved_hash = hash_command(spelling, args)
        source = f"{approved_hash}:{env_hash}"
        source_pair = launch_pair(approved_hash, derived_env_hash)
        if (
            source in approvals.approved.get(stem, ())
            and approvals.admits_launch(stem, approved_hash, derived_env_hash)
            and source_pair in approvals.approved_displays.get(stem, {})
            and saved_command_casing(spelling) == command
        ):
            return source, source_pair
    return None


def prefold_digests(
    declared_env: Mapping[Any, Any], expanded_env: Mapping[Any, Any]
) -> tuple[str, str]:
    """The env digests a store written before the ``PATH`` fold holds for one launch.

    The declared text and its expansion, hashed as written, as
    ``launch_fingerprint`` and the rewriter's derived hash did before the fold.
    """
    from kiro_crew.mcp_gateway.launch_approval import _coerced_env

    return hash_declared_env(_coerced_env(declared_env)), hash_declared_env(
        _coerced_env(expanded_env)
    )


def prefold_approval_of(
    approvals: LaunchApprovals,
    stem: str,
    fingerprint: str,
    prefold: tuple[str, str] | None,
) -> tuple[str, str] | None:
    """The pre-fold approval of this exact launch, or ``None``.

    *prefold* is :func:`prefold_digests` of the launch behind *fingerprint*.
    Its fingerprint and pair match only when the command, args and env are the
    same bytes the person approved, so the folded digest of that launch is the
    approved launch, not a widened one. A launch whose digest did not move
    (nothing folded) has nothing to carry over.
    """
    if prefold is None:
        return None
    from kiro_crew.mcp_gateway.launch_approval import launch_pair

    command_hash = fingerprint.partition(":")[0]
    declared, derived = prefold
    source = f"{command_hash}:{declared}"
    if source == fingerprint:
        return None
    if source in approvals.approved.get(stem, ()) and approvals.admits_launch(
        stem, command_hash, derived
    ):
        return source, launch_pair(command_hash, derived)
    return None


def without_sources(
    approvals: LaunchApprovals,
    stem: str,
    prefolded: tuple[str, str] | None,
    additions: Iterable[tuple[Mapping[str, Collection[Any]], Collection[Any]]],
) -> list[tuple[Mapping[str, Collection[Any]], Collection[Any]]]:
    """*additions* with every carried-over pre-fold source left out of its population.

    A carried-over launch replaces its source, so the per-server cap counts
    that launch once, not once per digest.
    """
    sources = {
        part
        for source in (prefolded, *approvals.prefold_healed.get(stem, {}).values())
        if source is not None
        for part in source
    }
    if not sources:
        return list(additions)
    return [
        ({stem: [x for x in population.get(stem, ()) if x not in sources]}, candidates)
        for population, candidates in additions
    ]


def superseded_sources(
    approvals: LaunchApprovals, stem: str, withdrawn: Collection[str]
) -> set[str]:
    """The pre-fold fingerprints and pairs a save replaces with their folded ones."""
    return {
        part
        for fp, source in approvals.prefold_healed.get(stem, {}).items()
        if fp not in withdrawn
        for part in source
    }


#: Audit reason for a pair admitted because only a ``${VAR}`` value changed.
REBIND_AUDIT_REASON = "approved launch, ${VAR} value changed"
#: Audit reason for a pair admitted because only the PATHEXT casing repair moved.
CASING_AUDIT_REASON = "approved launch, Windows command casing repaired"
#: Audit reason for a pair admitted under its approval from before the PATH fold.
PREFOLD_AUDIT_REASON = "approved launch, stored before the gateway PATH fold"
#: Audit reason for a changed launch of a server whose changes the operator trusts.
TRUST_AUDIT_REASON = "changed launch of a server the operator trusts"


def trusts_change(approvals: LaunchApprovals, stem: str, managed: bool) -> bool:
    """Whether a changed launch of *stem* is admitted because the operator trusts it.

    Only the dashboard's approve press sets the choice, and only with an
    approval: a stem with no approved launch, a never-pooled server or a
    managed launch is never admitted this way.
    """
    from kiro_crew.mcp_gateway.launch_approval import _unpoolable_stems

    # shortcut: each trusted change adds digests up to the per-server launch cap;
    # replace the older digests if a trusted server ever reaches it.
    return (
        not managed
        and stem in approvals.trusted
        and bool(approvals.approved.get(stem))
        and stem not in _unpoolable_stems()
    )


def captured_by_reason(
    approvals: LaunchApprovals, withheld: Mapping[str, set[str]]
) -> dict[str, dict[str, set[str]]]:
    """Split what a pass captured by why it was admitted, for the audit line.

    A rebound fingerprint is an operator-approved declaration whose ``${VAR}``
    expansion changed, a casing-healed one an approved launch whose command the
    PATHEXT casing repair rewrote, a pre-fold one an approval stored before the
    PATH fold, a trusted one a changed launch of a server the operator trusts;
    every other captured fingerprint is a managed launch. A fingerprint in
    *withheld* (stem -> fingerprints the save did not write) is left out.
    """
    by_reason: dict[str, dict[str, set[str]]] = {}
    for stem, fps in approvals.captured.items():
        rebound = set(approvals.rebound.get(stem, {}).values())
        for fingerprint in fps - withheld.get(stem, set()):
            reason = REBIND_AUDIT_REASON if fingerprint in rebound else "managed launch"
            if fingerprint in approvals.casing_healed.get(stem, {}):
                reason = CASING_AUDIT_REASON
            if fingerprint in approvals.prefold_healed.get(stem, {}):
                reason = PREFOLD_AUDIT_REASON
            if fingerprint in approvals.trust_admitted.get(stem, {}):
                reason = TRUST_AUDIT_REASON
            by_reason.setdefault(reason, {}).setdefault(stem, set()).add(fingerprint)
    return by_reason


def forget_unseen_trust(seen: LaunchApprovals, current: LaunchApprovals) -> bool:
    """Drop the trust choice of every server that a full pass did not find declared.

    A removed server keeps its name on the share list, so a server added again
    under that name would otherwise inherit the choice. A server still declared
    keeps it, whatever this pass did with its launch. Only a complete full pass
    can say a server is gone: a pass served from cache, or one that skipped an
    agent, drops nothing. True when one was dropped.
    """
    if not seen.full_pass or seen.live_incomplete:
        return False
    gone = current.trusted - seen.declared
    current.trusted -= gone
    return bool(gone)


def forget_trust(names: Iterable[str], path: Path | None = None) -> None:
    """Drop the trust choice of *names*: their sharing was turned off in the dashboard."""
    from kiro_crew.mcp_gateway import launch_approval as store

    stems = {store.target_stem(n) for n in names if isinstance(n, str) and n}
    target = path or store.approvals_path()
    with store._STORE_LOCK:
        current = store.load_approvals(target)
        if not stems & current.trusted:
            return
        current.trusted -= stems
        store._write(target, store._document(current, current.stored_refused))
