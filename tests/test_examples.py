"""What the team fills in on the day must run: the worked example and the kit seam."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from hacklab import agent
from hacklab.config import Config
from hacklab.event import adapter
from hacklab.models import Task
from hacklab.tracking import Tracker

EXAMPLES = Path(__file__).parents[1] / "examples"


def load(name):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def audit(tmp_path):
    rows = [("a1", "login"), ("a1", "exec_payload"), ("a2", "exec_payload"), ("a3", "login")]
    (tmp_path / "events.jsonl").write_text(
        "".join(
            json.dumps({"ts": f"2026-11-14T01:0{i}:00Z", "who": who, "what": what}) + "\n"
            for i, (who, what) in enumerate(rows)
        )
    )
    return tmp_path


def test_the_playbook_template_answers_from_whatever_table_holds_the_value(audit, monkeypatch):
    module = load("event_playbooks")
    monkeypatch.setitem(sys.modules, "event_playbooks", module)
    config = Config()
    config.agent.playbooks = "event_playbooks:playbooks"
    tracker = Tracker(audit / "state" / "t.sqlite")
    task = Task(
        id="count",
        question="How many events with exec_payload are in the log?",
        documents=["events.jsonl"],
    )
    run_id, answer = agent.run_task(task, audit, config, tracker)
    assert answer.status == "ok" and answer.answer == "2"
    assert [c.locator for c in answer.citations] == ["line:2", "line:3"]
    assert tracker.summary(run_id)["calls"] == 0
    # A value that occurs nowhere is not guessed at: the solver steps aside.
    unknown = Task(
        id="none",
        question="How many events with wipe_disk are in the log?",
        documents=["events.jsonl"],
    )
    _, answer = agent.run_task(unknown, audit, config, tracker)
    assert answer.status == "abstain"


def test_the_kit_adapter_runs_a_task_with_the_kits_tools(audit, tmp_path):
    (audit / "note.txt").write_text("Warehouse opens at 09:00.\n")
    config = tmp_path / "mock.toml"
    config.write_text('[model]\nprovider = "mock"\n')

    def get_agent_log(agent_id):
        """Rows of one agent."""
        return []

    common = {"config_path": config, "tracking": tmp_path / "state" / "t.sqlite"}
    result = adapter.solve(
        "Return the source line.",
        task_id="t1",
        data_dir=audit,
        documents=["note.txt"],
        kit_tools=[get_agent_log],
        **common,
    )
    assert result == {
        "status": "ok",
        "answer": "Warehouse opens at 09:00.",
        "sources": [
            {"document": "note.txt", "locator": "line:1", "quote": "Warehouse opens at 09:00."}
        ],
        "actions": [],
    }
    # A task the kit hands over in a shape nobody expected still gets a result.
    for question, task_id in (("", "t2"), ("Return it.", ""), ("x" * 50000, 7)):
        result = adapter.solve(question, task_id=task_id, data_dir=audit, **common)
        assert result["status"] in ("abstain", "error") and result["answer"] == ""


def test_the_kit_adapter_takes_the_kits_model_and_data_from_its_tools(tmp_path):
    data = tmp_path / "data"
    config = tmp_path / "kit.toml"
    config.write_text('[model]\nprovider = "openai"\nmodel = "kit-model"\n')
    # Rows a kit tool returned become a document that can be read and cited.
    rows = [{"agent": "agent-7", "state": "compromised"}, {"agent": "agent-9", "state": "clean"}]
    name = adapter.snapshot(data, "agents", rows)
    assert name == "agents.jsonl"
    assert adapter.snapshot(data, "notes/free.txt", ["plain text"]) == "notes/free.txt"
    seen = []

    def chat(messages):
        seen.append(messages)
        if len(seen) == 1:
            arguments = {"document": "agents.jsonl", "where": {"state": "compromised"}}
            return json.dumps({"action": "table", "arguments": arguments})
        result = {"status": "ok", "answer": "agent-7", "evidence": ["e1"], "confidence": 0.9}
        return {"choices": [{"message": {"content": json.dumps({"action": "final", **result})}}]}

    result = adapter.solve(
        "Which agent is compromised?",
        task_id="t1",
        data_dir=data,
        documents=None,  # Every document below the data directory.
        model=chat,
        config_path=config,
        tracking=tmp_path / "state" / "t.sqlite",
    )
    assert result["status"] == "ok" and result["answer"] == "agent-7" and len(seen) == 2
    assert [source["document"] for source in result["sources"]] == ["agents.jsonl"]
    assert "agents.jsonl" in seen[0][1]["content"] and "notes/free.txt" in seen[0][1]["content"]
