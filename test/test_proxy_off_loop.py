"""Async callers resolve the system proxy off the event loop.

``http_proxy.is_bypassed`` can consult the OS bypass matcher, which on Windows
(``proxy_bypass_registry``) can block on a DNS lookup. Each async entry that
builds a Slack or Telegram client goes through ``resolve_proxy_off_loop``, so
that matcher never runs on the loop thread. The matcher is replaced here by one
that records the thread it runs on; any entry that resolves inline fails.
"""

from __future__ import annotations

import asyncio
import threading
import urllib.request
from typing import Any

import pytest

from kiro_crew import http_proxy

_PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")


@pytest.fixture
def os_matcher_threads(monkeypatch: Any) -> list[int]:
    """NO_PROXY alone plus an OS proxy, so every resolution reaches the OS matcher."""
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(urllib.request, "getproxies_environment", lambda: {"no": "example.test"})
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    monkeypatch.setattr(http_proxy, "_os_proxies", lambda: {"https": "http://127.0.0.1:3128"})
    threads: list[int] = []

    def matcher(host: str) -> bool:
        threads.append(threading.get_ident())
        return False

    monkeypatch.setattr(http_proxy, "_os_bypass", lambda: matcher)
    return threads


def _run_on_loop(coro_fn: Any) -> tuple[Any, int]:
    async def main() -> tuple[Any, int]:
        return await coro_fn(), threading.get_ident()

    return asyncio.run(main())


def test_the_shared_helper_runs_the_os_matcher_off_the_loop(os_matcher_threads: list[int]) -> None:
    proxy, loop_thread = _run_on_loop(
        lambda: http_proxy.resolve_proxy_off_loop(http_proxy.SLACK_API_URL)
    )
    assert proxy == "http://127.0.0.1:3128"
    assert os_matcher_threads and loop_thread not in os_matcher_threads


def test_the_shared_access_client_resolves_off_the_loop(os_matcher_threads: list[int]) -> None:
    from kiro_crew.dashboard import slack_shared_access

    client, loop_thread = _run_on_loop(lambda: slack_shared_access._web_client("xoxb-test"))
    assert client.proxy == "http://127.0.0.1:3128"
    assert os_matcher_threads and loop_thread not in os_matcher_threads


def test_the_slack_token_check_resolves_off_the_loop(
    os_matcher_threads: list[int], monkeypatch: Any
) -> None:
    import slack_sdk.web.async_client as async_client

    from kiro_crew.dashboard.messaging_api import slack_settings

    built: list[Any] = []

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

        async def auth_test(self) -> dict[str, Any]:
            return {"ok": True}

    monkeypatch.setattr(async_client, "AsyncWebClient", _Client)
    verdict, loop_thread = _run_on_loop(
        lambda: slack_settings._validate_slack_token("SLACK_BOT_TOKEN", "xoxb-test")
    )
    assert verdict is None
    assert built[0]["proxy"] == "http://127.0.0.1:3128"
    assert os_matcher_threads and loop_thread not in os_matcher_threads


def test_the_boot_path_builds_the_slack_client_without_a_proxy_lookup() -> None:
    # no-new-work-on-gateway-boot-path: _init_services runs before the dashboard
    # binds, so the lookup waits for run() past KIROCREW_READY (use_proxy).
    import inspect

    from kiro_crew.slack.gateway_runtime import service_boot

    assert "resolve_proxy" not in inspect.getsource(service_boot._init_services)
    run_src = inspect.getsource(service_boot.run)
    ready = run_src.index("KIROCREW_READY:")
    applied = run_src.index("self.slack.use_proxy(await resolve_proxy_off_loop(SLACK_API_URL))")
    assert ready < applied < run_src.index("await init_socket_mode(self, seen)")


def test_use_proxy_routes_later_web_api_calls(monkeypatch: Any) -> None:
    from kiro_crew.slack.client import RealSlackClient

    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
    client = RealSlackClient("xoxb-test")
    client.use_proxy("http://127.0.0.1:3128")
    assert client._web.proxy == "http://127.0.0.1:3128"


def test_the_telegram_proxy_resolves_off_the_loop(os_matcher_threads: list[int]) -> None:
    from kiro_crew.telegram import client_http

    proxy, loop_thread = _run_on_loop(client_http._resolve_proxy_off_loop)
    assert proxy == "http://127.0.0.1:3128"
    assert os_matcher_threads and loop_thread not in os_matcher_threads
