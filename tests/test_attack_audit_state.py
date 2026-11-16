"""Offline regression for uncertain mutation outcomes.

A lost reply is an open outcome: the identical call is not sent a second time.
The test uses a recording transport; no platform or provider is contacted.
"""

import json
from pathlib import Path

import httpx
import pytest

from hacklab.config import load_config
from hacklab.event.arena import Arena
from hacklab.event.arena_run import arena_tools
from hacklab.evidence import Ledger
from hacklab.toolkit import Reloaded, Toolbox
from hacklab.tools import ToolRegistry


@pytest.mark.parametrize("lose_first_reply", [False, True])
def test_lost_reply_does_not_make_identical_mutation_repeatable(tmp_path, lose_first_reply):
    config = load_config(Path(__file__).parents[1] / "configs/live.toml")
    assert config.agent.fallback == "none"
    request = "Send one status message to soc@hub.example."
    arguments = {"to": "soc@hub.example", "subject": "Status", "body": "Review complete."}
    calls = []
    reloads = []

    def handle(request):
        assert request.url.path.endswith("/tools/send_message")
        calls.append(json.loads(request.content))  # The synthetic side effect happened.
        if lose_first_reply and len(calls) == 1:
            raise httpx.ReadTimeout("synthetic lost reply")
        return httpx.Response(200, json={"ok": True, "result": "sent"})

    def reload():
        reloads.append(True)
        return Reloaded(documents=[])

    client = httpx.Client(transport=httpx.MockTransport(handle))
    arena = Arena("https://arena.example", "synthetic", client=client, sleep=lambda _: None)
    try:
        specs = arena_tools(
            arena,
            "synthetic-run",
            "synthetic-task",
            [{"name": "send_message", "args": {"to": "recipient", "body": "text"}}],
            actions=True,
            trusted={"soc@hub.example"},
        )
        toolbox = Toolbox(
            ToolRegistry(tmp_path), Ledger(config.guard, request), config, specs, reload
        )
        first = toolbox.execute("send_message", arguments)
        assert calls == [arguments]  # The HTTP client itself did not retry a lost reply.
        assert len(reloads) == 1
        second = toolbox.execute("send_message", arguments)
        if lose_first_reply:
            assert "outcome unknown" in first.text(2000)
            assert second.error is not None
            assert "already attempted" in second.text(2000)
            assert calls == [arguments]
            assert len(reloads) == 1
        else:
            assert first.error is None
            assert "already_done" in second.text(2000)
            assert calls == [arguments]
            assert len(reloads) == 1
    finally:
        arena.close()
