"""What one held Bot limit covers: a whole capability, one tool, or some paths.

``mate_held_rules`` records each limit a moved Bot's worker cannot carry, and its
calls ask a person even under Trust. A limit is recorded as narrowly as Crew can
read it, so it holds the calls it speaks about and not the Bot's others:

- a hook whose matcher names one tool Crew has no capability for (``use_aws``)
  is recorded as ``tool:<name>`` and holds that tool's calls only;
- a ``permissions`` rule that denies (or asks for) reads or writes of named
  paths, and a ``toolsSettings`` entry whose only limit is ``deniedPaths``,
  keep those paths, and hold only the reads or writes that touch them.

Every narrowing fails closed. A matcher that names more than one tool, a rule or
setting that limits more than paths, and a call whose target cannot be told (no
path, a variable, a relative path with no folder to read it in, a scan cut short)
are held as before. Blocking file I/O (``realpath``); never raises.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from collections.abc import Iterable, Mapping
from typing import Any

#: The capability prefix of a limit that covers one tool no capability names.
TOOL_PREFIX = "tool:"

#: A tool name as a matcher or an ``allowedTools`` entry spells exactly one tool.
_ONE_TOOL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")

#: The capabilities a held limit may be narrowed to paths for.
_PATH_CAPABILITIES = frozenset({"fs_read", "fs_write"})

#: The effects of a ``permissions`` rule that limit a call.
_LIMITING_EFFECTS = frozenset({"deny", "ask"})

_GLOB = re.compile(r"[*?\[]")


def named_tool(name: str) -> str:
    """``tool:<name>`` when *name* is one tool's name, ``""`` otherwise."""
    return TOOL_PREFIX + name if isinstance(name, str) and _ONE_TOOL.match(name) else ""


def rule_paths(capability: str, rule: Any) -> list[str] | None:
    """The paths a held ``permissions`` *rule* limits, ``None`` when it limits more."""
    if capability not in _PATH_CAPABILITIES or not isinstance(rule, dict):
        return None
    if set(rule) - {"capability", "effect", "match"} or rule.get("effect") not in _LIMITING_EFFECTS:
        return None
    return _strings(rule.get("match"))


def settings_paths(capability: str, entry: Any, grant_keys: Iterable[str]) -> list[str] | None:
    """The paths a held ``toolsSettings`` *entry* limits, ``None`` when it limits more.

    Its grant keys (*grant_keys*, such as ``allowedPaths``) only let calls run, so
    only an entry whose one limiting key is ``deniedPaths`` is read as a path limit.
    """
    if capability not in _PATH_CAPABILITIES or not isinstance(entry, dict):
        return None
    if set(entry) - set(grant_keys) != {"deniedPaths"}:
        return None
    return _strings(entry.get("deniedPaths"))


def _strings(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        return None
    return list(value)


def covers_tool(capability: str, names: set[str]) -> bool:
    """Whether a ``tool:<name>`` limit covers a call the harness named *names*."""
    return capability[len(TOOL_PREFIX) :] in names


def covers_paths(paths: list[str], event: Any) -> bool:
    """Whether a call *event* touches one of *paths*, or its targets cannot be told."""
    from kiro_crew.platform.tool_paths import target_paths

    raw = getattr(event, "raw_tool_params", None)
    found = target_paths(raw if isinstance(raw, Mapping) else None)
    if found.truncated or found.unanchored:
        return True
    targets = list(found)
    diff_path = getattr(event, "diff_path", "") or ""
    if isinstance(diff_path, str) and diff_path:
        targets.append(diff_path)
    if not targets:
        return True
    cwd = str(getattr(event, "spec_write_cwd", "") or "")
    for target in targets:
        resolved = _resolved(target, cwd)
        if resolved is None or any(_touches(resolved, p) for p in paths):
            return True
    return False


def _resolved(path: str, cwd: str) -> str | None:
    """*path* made absolute and real, ``None`` when what it names cannot be told."""
    if os.path.expandvars(path) != path:
        return None
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        if not cwd or not os.path.isabs(cwd):
            return None
        expanded = os.path.join(cwd, expanded)
    return _fold(os.path.realpath(expanded))


def _fold(path: str) -> str:
    # macOS and Windows file systems ignore case by default: compare without it.
    path = os.path.normcase(path)
    return path.casefold() if sys.platform in ("darwin", "win32") else path


def _touches(target: str, pattern: str) -> bool:
    """Whether a call on *target* reaches what *pattern* names.

    It does when *target* is that path, lies below it, or is a folder above it (a
    read of the folder may reach the file). A relative pattern names its path in
    any folder.
    """
    if os.path.expandvars(pattern) != pattern:
        return True
    expanded = os.path.expanduser(pattern)
    if _GLOB.search(expanded):
        spelled = _fold(expanded)
        # ``spelled`` uses the platform's separator (``normcase``), so the "any
        # folder" prefix must too, or a relative glob matches nothing on Windows.
        anywhere = "*" + os.sep + spelled
        if fnmatch.fnmatchcase(target, spelled) or fnmatch.fnmatchcase(target, anywhere):
            return True
        # The folder the pattern starts in: a read of it, or of a folder above it,
        # may reach a file the pattern matches.
        head = _GLOB.split(expanded, 1)[0]
        folder = head[: max(head.rfind("/"), head.rfind(os.sep)) + 1]
        if not folder or not os.path.isabs(folder):
            return False
        literal = _fold(os.path.realpath(folder))
        return target == literal or _above(target, literal)
    if not os.path.isabs(expanded):
        tail = _fold(os.path.normpath(expanded))
        return target == tail or target.endswith(os.sep + tail)
    named = _fold(os.path.realpath(expanded))
    return target == named or _above(target, named) or _above(named, target)


def _above(folder: str, path: str) -> bool:
    """Whether *path* lies inside *folder*."""
    root = folder.rstrip(os.sep) + os.sep
    return path.startswith(root)
