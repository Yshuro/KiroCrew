# The owner can trust one shared MCP server's launch changes, from any source

Decided by: Ray Xu (maintainer, @buluoray)
Date: 2026-11-23
Supersedes: 2026-11-20-the-gateways-own-path-is-not-a-launch-change.md (in part)

## Decision

"Always trust changes to this server" is offered for every shared MCP server,
whatever file declares it, opt-in per server and unchecked by default, and only
the owner's click in the dashboard sets it; once set, later command, argument or
env changes to that server, including changes an agent makes, stay shared without
asking again. For servers the owner marked trusted this replaces the 2026-11-20
line that a changed command or argument stays held until agents can no longer
write the MCP registry; every other server keeps that rule, and the rest of the
2026-11-20 entry stands.

## Why

- Servers that update themselves re-ask after every update, and people approve
  them without reading.
- The owner makes the choice per server, so nobody is opted in by default.

## Evidence

- https://github.com/kirodotdev/KiroCrew/pull/41444#issuecomment-7114051793 --
  Ray Xu's comment stating this decision on the pull request that builds it.
