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
