"""Disconnect registered crews before the managed-environment lane deletes their records."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from aiohttp import web

from kiro_crew.dashboard.handlers_instances_gate import _CHAIN_MUTATION_LOCK, _registry
from kiro_crew.instances.registry import descendant_ids

if TYPE_CHECKING:
    from kiro_crew.dashboard.state import DashboardState

logger = logging.getLogger(__name__)


async def disconnect_registered(
    state: DashboardState, registered: str, tag: str
) -> web.Response | None:
    """Keep recovery rows and connect intent when any captured tunnel stop fails."""
    from kiro_crew.dashboard.handlers_cloud import _audit

    mgr = getattr(state, "instances_manager", None)
    if not registered or mgr is None:
        return None
    async with _CHAIN_MUTATION_LOCK:
        instances = await asyncio.to_thread(_registry(state).list)
        for row in instances:
            if registered not in (row.ssm_target, row.ssh_host, row.id):
                continue
            # Name each descendant: the manager's parent cascade suppresses child failures.
            for cid in [*reversed(descendant_ids(instances, row.id)), row.id]:
                try:
                    await mgr.disconnect(cid, keep_intent=True)
                except Exception as exc:
                    _audit("destroy", "denied", request_id=tag, error="teardown_failed")
                    logger.warning(
                        "Refusing to delete %s: disconnecting %s failed (%s)",
                        tag,
                        cid,
                        type(exc).__name__,
                    )
                    return web.json_response(
                        {
                            "error": (
                                f"Crew {cid} could not be disconnected. Nothing was deleted. "
                                "Retry once its tunnel is down."
                            ),
                            "code": "remove_teardown_failed",
                        },
                        status=409,
                    )
    return None
