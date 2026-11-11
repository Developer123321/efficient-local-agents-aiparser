import json
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

from hacklab.tracking import Tracker


def tracker(tmp_path):
    return Tracker(tmp_path / "private" / "runs.sqlite")


def test_private_receipts_drop_bodies_and_credentials(tmp_path):
    tracking = tracker(tmp_path)
    secret = "super-secret-request-value"
    run = tracking.start_run(
        "case-a",
        {
            "model": {
                "provider": "openai",
                "model": "small-model",
                "base_url": f"https://username:{secret}@example.test/{secret}?key={secret}",
                "api_key": secret,
            },
            "prompt": secret,
        },
        metadata={"git_head": "a" * 40, "nested": {"password": secret}},
    )
    tracking.event(run, "step", {"step": 1, "action": "search", "arguments": secret})
    tracking.record_call(
        run,
        "openai",
        "small-model",
        30,
        5,
        12,
        "ok",
        1,
        2,
        response_body=secret,
        request_headers={"authorization": secret},
    )
    tracking.finish_run(run, "ok", {"answer": secret}, 1.0)
    destination = tmp_path / "export.json"
    tracking.export(destination)
    exported = destination.read_text()
    assert secret not in exported
    assert "username" not in exported
    assert tracking.runs()[0]["config"]["model"]["origin"] == "https://example.test"
    assert tracking.runs()[0]["answer_hash"]
    assert stat.S_IMODE(tracking.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(tracking.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert "super-secret" not in tracking.path.read_bytes().decode(errors="ignore")


def test_failures_and_retries_remain_unknown_not_free(tmp_path):
    tracking = tracker(tmp_path)
    run = tracking.start_run("case-a", {}, mode="live")
    tracking.record_call(run, "openai", "small", None, None, 20, "timeout", 1, 2, attempt=1)
    tracking.record_call(run, "openai", "small", 100, 20, 10, "ok", 1, 2, attempt=2)
    summary = tracking.summary(run)
    assert summary["calls"] == 2
    assert summary["unknown_cost_calls"] == 1
    assert summary["cost_usd"] is None
    assert summary["known_cost_usd"] == pytest.approx(0.00014)
    assert summary["input_tokens"] is None
    assert summary["total_tokens"] is None
    assert summary["known_input_tokens"] == 100
    assert not summary["usage_complete"]
    assert tracking.calls(run)[0]["metadata"] == {"attempt": 1}


def test_missing_prices_unknown_usage_reported_and_mock_separate(tmp_path):
    tracking = tracker(tmp_path)
    live = tracking.start_run("live-case", {}, mode="live")
    mock = tracking.start_run("mock-case", {})
    tracking.record_call(live, "jev", "jev-1.14.0", 20, 0, 2, "ok")
    tracking.record_call(mock, "mock", "fixture", 0, 0, 0, "ok")
    assert tracking.summary(live)["cost_usd"] is None
    assert tracking.summary(live)["input_tokens"] == 20
    assert tracking.summary(mock)["cost_usd"] == 0
    assert tracking.summary(mock)["mock_calls"] == 1
    assert tracking.summary()["run_count"] == 2


def test_both_providers_and_prices_sum_independently(tmp_path):
    tracking = tracker(tmp_path)
    run = tracking.start_run("case-a", {})
    tracking.record_call(run, "openai", "small", 1000, 200, 50, "ok", 0.2, 0.4)
    tracking.record_call(run, "jev", "jev-1.14.0", 500, 0, 8, "ok", 0.042, 0)
    summary = tracking.summary(run)
    assert summary["cost_usd"] == pytest.approx(0.000301)
    assert summary["total_tokens"] == 1700
    assert summary["latency_ms"] == 58
    assert summary["usage_complete"]


def test_concurrent_writers_and_foreign_keys(tmp_path):
    tracking = tracker(tmp_path)
    run = tracking.start_run("case-a", {})

    def write(index):
        other = Tracker(tracking.path)
        other.record_call(run, "mock", "fixture", 0, 0, 0, "ok", attempt=index)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(20)))
    assert tracking.summary(run)["calls"] == 20
    with pytest.raises(sqlite3.IntegrityError):
        tracking.event("missing", "test", {})


@pytest.mark.parametrize("value", [-1, 1.2, True])
def test_invalid_usage_rejected(tmp_path, value):
    tracking = tracker(tmp_path)
    run = tracking.start_run("case-a", {})
    with pytest.raises(ValueError):
        tracking.record_call(run, "openai", "small", value, 0, 0, "ok")


def test_symlinks_rejected_and_exports_preserve_database(tmp_path):
    tracking = tracker(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(tracking.path)
    with pytest.raises(ValueError):
        tracking.export(link)
    with pytest.raises(ValueError):
        tracking.export(tracking.path)
    hardlink = tmp_path / "hardlink.json"
    hardlink.hardlink_to(tracking.path)
    with pytest.raises(ValueError):
        tracking.export(hardlink)
    with pytest.raises(ValueError):
        tracking.export(tracking.path.with_name(tracking.path.name + "-wal"))
    run = tracking.start_run("case-a", {})
    tracking.finish_run(run, "abstain")
    assert tracking.runs()[0]["status"] == "abstain"


def test_export_json_contains_inspectable_events_without_arbitrary_keys(tmp_path):
    tracking = tracker(tmp_path)
    run = tracking.start_run("case-a", {})
    tracking.event(
        run,
        "verification",
        {"verified": True, "reason_code": "citations_ok", "quote": "private source", "score": 1.0},
    )
    target = tmp_path / "export.json"
    tracking.export(target)
    data = json.loads(target.read_text())
    assert data["events"][0]["data"] == {
        "verified": True,
        "reason_code": "citations_ok",
        "score": 1.0,
    }
    assert "not a provider invoice" in data["cost_basis"]


def test_actual_agent_metadata_survives_receipt_allowlist(tmp_path):
    from hacklab.agent import _metadata

    metadata = _metadata()
    tracking = tracker(tmp_path)
    tracking.start_run("source-identity", {}, metadata=metadata)
    recorded = tracking.runs()[0]["metadata"]
    assert recorded == metadata
    assert len(recorded["source_sha256"]) == 64
