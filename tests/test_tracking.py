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
    assert "neither is an invoice" in data["cost_basis"]


def test_actual_agent_metadata_survives_receipt_allowlist(tmp_path):
    from hacklab.agent import _metadata

    metadata = _metadata()
    tracking = tracker(tmp_path)
    tracking.start_run("source-identity", {}, metadata=metadata)
    recorded = tracking.runs()[0]["metadata"]
    assert recorded == metadata
    assert len(recorded["source_sha256"]) == 64


def test_reported_cost_is_stored_apart_and_validated(tmp_path):
    tracking = tracker(tmp_path)
    first, second = tracking.start_run("case-a", {}), tracking.start_run("case-b", {})
    tracking.record_call(first, "openai", "small", 1000, 200, 5, "ok", 0.2, 0.4, 0.0005)
    tracking.record_call(first, "openai", "small", 1000, 200, 5, "ok", 0.2, 0.4, reported_cost=0)
    tracking.record_call(second, "openai", "small", 10, 2, 5, "ok", reported_cost=0.25)
    tracking.record_call(second, "mock", "fixture", 0, 0, 0, "ok")
    rows = tracking.calls(first)
    assert [row["reported_cost_usd"] for row in rows] == [0.0005, 0.0]
    assert rows[0]["cost_usd"] == pytest.approx(0.00028)
    summary = tracking.summary(first)
    assert summary["cost_usd"] == pytest.approx(0.00056)
    assert summary["reported_cost_usd"] == summary["spent_usd"] == pytest.approx(0.0005)
    # Without prices there is no estimate, and the reported figure still counts as spend.
    other = tracking.summary(second)
    assert other["cost_usd"] is None and other["spent_usd"] == pytest.approx(0.25)
    assert other["reported_cost_calls"] == 1 and other["mock_calls"] == 1
    both = tracking.tally([first, second])
    assert both["calls"] == 4 and both["run_count"] == 2
    assert both["spent_usd"] == pytest.approx(0.2505) and tracking.tally([])["calls"] == 0
    for wrong in (-0.1, float("nan"), float("inf"), "0.1", True):
        with pytest.raises(ValueError):
            tracking.record_call(first, "openai", "small", 1, 1, 0, "ok", reported_cost=wrong)
    target = tmp_path / "exports" / "runs.json"
    tracking.export(target)
    exported = json.loads(target.read_text())
    assert exported["calls"][0]["reported_cost_usd"] == 0.0005
    assert exported["summary"]["known_reported_cost_usd"] == pytest.approx(0.2505)


def test_a_database_from_before_reported_cost_stays_readable(tmp_path):
    path = tmp_path / "private" / "runs.sqlite"
    path.parent.mkdir()
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, mode TEXT NOT NULL,
            config_hash TEXT NOT NULL, config_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL, answer_hash TEXT, score REAL
        );
        CREATE TABLE calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(id),
            created_at TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
            input_tokens INTEGER, output_tokens INTEGER,
            latency_ms REAL NOT NULL, status TEXT NOT NULL,
            input_rate REAL, output_rate REAL, cost_usd REAL,
            billable INTEGER NOT NULL, metadata_json TEXT NOT NULL
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(id),
            created_at TEXT NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL
        );
        INSERT INTO runs VALUES
            ('old', 'case', 't', NULL, 'ok', 'live', 'h', '{}', '{}', NULL, NULL);
        INSERT INTO calls (run_id, created_at, provider, model, input_tokens, output_tokens,
            latency_ms, status, input_rate, output_rate, cost_usd, billable, metadata_json)
            VALUES ('old', 't', 'openai', 'small', 100, 20, 5, 'ok', 1, 2, 0.00014, 1, '{}');
    """)
    old.commit()
    old.close()
    tracking = Tracker(path)
    Tracker(path)  # Opening it again finds the column and adds nothing.
    # The old receipt keeps its estimate; what the provider reported then is unknown.
    assert tracking.calls("old")[0]["reported_cost_usd"] is None
    summary = tracking.summary("old")
    assert summary["cost_usd"] == pytest.approx(0.00014) and summary["reported_cost_usd"] is None
    assert summary["spent_usd"] == pytest.approx(0.00014)
    assert tracking.spend()["usd"] == pytest.approx(0.00014)
    tracking.record_call("old", "openai", "small", 100, 20, 5, "ok", 1, 2, reported_cost=0.0002)
    assert tracking.summary("old")["spent_usd"] == pytest.approx(0.00034)
