import json
import sys

from hacklab.cli import main


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
