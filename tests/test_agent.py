import json

import httpx
import pytest

from hacklab import agent
from hacklab.config import Config, Limits, ModelConfig
from hacklab.models import Task
from hacklab.providers import SmallModelProvider
from hacklab.tracking import Tracker


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("SMALL_MODEL_API_KEY", "synthetic-key")
    root = tmp_path / "docs"
    root.mkdir()
    tracker = Tracker(tmp_path / "state" / "runs.sqlite3")
    return root, tracker


def final(answer="42", citations=None):
    return {
        "action": "final",
        "arguments": {},
        "result": {
            "status": "ok",
            "answer": answer,
            "citations": citations or [],
            "confidence": 0.9,
            "reason": "Supported by the evidence.",
        },
    }


def live(monkeypatch, decisions, *, limits=None, http_status=200):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        decision = decisions[min(len(calls) - 1, len(decisions) - 1)]
        payload = {
            "choices": [{"message": {"content": json.dumps(decision)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        return httpx.Response(http_status, json=payload)

    def providers(config, budget, tracker, run_id):
        return (
            SmallModelProvider(
                config.model,
                config.limits,
                budget,
                tracker,
                run_id,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            ),
            None,
        )

    monkeypatch.setattr(agent, "make_providers", providers)
    config = Config(
        model=ModelConfig(provider="openai", model="event-model", base_url="http://localhost/v1"),
        limits=limits or Limits(),
    )
    return config, calls


def test_offline_arithmetic_exercises_tools_without_api(lab):
    root, tracker = lab
    run_id, answer = agent.run_task(
        Task(id="arithmetic", question="Calculate 2 + 3"), root, Config(), tracker
    )
    assert answer.status == "ok"
    assert answer.answer == "5"
    assert tracker.summary(run_id)["calls"] == 0
    assert tracker.runs()[0]["mode"] == "mock"


def test_offline_single_line_is_explicit_plumbing(lab):
    root, tracker = lab
    (root / "note.txt").write_text("Warehouse opens at 09:00.\n")
    _, answer = agent.run_task(
        Task(id="one-line", question="Return the source line.", documents=["note.txt"]),
        root,
        Config(),
        tracker,
    )
    assert answer.status == "ok"
    assert answer.citations[0].quote == "Warehouse opens at 09:00."
    assert "without semantic judgment" in answer.reason
    assert answer.confidence == 0


def test_offline_conflicting_documents_abstain(lab):
    root, tracker = lab
    (root / "old.txt").write_text("Total: 10\n")
    (root / "new.txt").write_text("Total: 20\n")
    _, answer = agent.run_task(
        Task(id="conflict", question="What is the total?", documents=["old.txt", "new.txt"]),
        root,
        Config(),
        tracker,
    )
    assert answer.status == "abstain"


@pytest.mark.parametrize("text", ["\n", "   \n"])
def test_offline_blank_source_abstains_instead_of_crashing(lab, text):
    root, tracker = lab
    (root / "empty.txt").write_text(text)
    _, answer = agent.run_task(
        Task(id="empty", question="What is the value?", documents=["empty.txt"]),
        root,
        Config(),
        tracker,
    )
    assert answer.status == "abstain"


def test_jev_receives_calculator_evidence_without_requiring_quotes():
    class Judge:
        state = None
        questions = None

        def judge(self, state, questions):
            self.state = state
            self.questions = questions
            return dict.fromkeys(questions, 0.95)

    judge = Judge()
    task = Task(id="arithmetic", question="2+3")
    answer = agent.Answer.model_validate(final("5")["result"])
    assert agent._jev_verify(judge, answer, task, Config(), [{"value": "5"}])
    assert judge.state["tool_observations"] == [{"value": "5"}]
    assert "deterministic tool observations" in judge.questions["grounded"]["instructions"]


def test_jev_does_not_require_document_quotes_for_no_document_task():
    class Judge:
        def judge(self, state, questions):
            assert "grounded" not in questions
            return dict.fromkeys(questions, 0.95)

    assert agent._jev_verify(
        Judge(),
        agent.Answer.model_validate(final()["result"]),
        Task(id="no-documents", question="Synthetic?"),
        Config(),
        [],
    )


def test_read_then_grounded_final(lab, monkeypatch):
    root, tracker = lab
    (root / "note.txt").write_text("Budget approved: 42 EUR.\n")
    citation = {"document": "note.txt", "locator": "line:1", "quote": "Budget approved: 42 EUR."}
    config, calls = live(
        monkeypatch,
        [
            {"action": "read", "arguments": {"document": "note.txt"}, "result": None},
            final("42 EUR", [citation]),
        ],
    )
    run_id, answer = agent.run_task(
        Task(id="budget", question="Approved budget?", documents=["note.txt"]),
        root,
        config,
        tracker,
    )
    assert answer.status == "ok"
    assert answer.answer == "42 EUR"
    assert len(calls) == 2
    assert "Budget approved" in calls[1]["messages"][-1]["content"]
    assert tracker.summary(run_id)["calls"] == 2


def test_false_quote_cannot_pass_final_validation(lab, monkeypatch):
    root, tracker = lab
    (root / "note.txt").write_text("Budget approved: 42 EUR.\n")
    bad = {"document": "note.txt", "locator": "line:1", "quote": "Budget approved: 99 EUR."}
    config, calls = live(monkeypatch, [final("99 EUR", [bad])])
    _, answer = agent.run_task(
        Task(id="fabrication", question="Budget?", documents=["note.txt"]), root, config, tracker
    )
    assert answer.status == "abstain"
    assert len(calls) == 2


def test_citation_to_unlisted_document_cannot_pass(lab, monkeypatch):
    root, tracker = lab
    (root / "allowed.txt").write_text("Public number: 1\n")
    (root / "other.txt").write_text("Private number: 2\n")
    bad = {"document": "other.txt", "locator": "line:1", "quote": "Private number: 2"}
    config, _ = live(monkeypatch, [final("2", [bad])])
    _, answer = agent.run_task(
        Task(id="scope", question="Number?", documents=["allowed.txt"]), root, config, tracker
    )
    assert answer.status == "abstain"


def test_malformed_decision_gets_one_bounded_repair(lab, monkeypatch):
    root, tracker = lab
    config, calls = live(monkeypatch, [{"action": "final"}, final("42")])
    _, answer = agent.run_task(
        Task(id="repair", question="Synthetic response?"), root, config, tracker
    )
    assert answer.status == "ok"
    assert len(calls) == 2
    assert "repair" in calls[1]["messages"][-1]["content"]


def test_loop_step_limit_stops_repeated_tools(lab, monkeypatch):
    root, tracker = lab
    config, calls = live(
        monkeypatch,
        [{"action": "search", "arguments": {"query": "missing"}, "result": None}],
        limits=Limits(max_steps=2),
    )
    run_id, answer = agent.run_task(
        Task(id="loop", question="Find missing data."), root, config, tracker
    )
    assert answer.status == "abstain"
    assert answer.reason == "Maximum planning steps reached."
    assert len(calls) == 2
    assert tracker.summary(run_id)["calls"] == 2


def test_http_auth_failure_does_not_trigger_model_repair(lab, monkeypatch):
    root, tracker = lab
    config, calls = live(monkeypatch, [final()], http_status=401)
    _, answer = agent.run_task(Task(id="auth", question="Synthetic?"), root, config, tracker)
    assert answer.status == "error"
    assert answer.reason == "provider_http_401"
    assert len(calls) == 1


def test_context_limit_refuses_oversized_task_before_api(lab, monkeypatch):
    root, tracker = lab
    config, calls = live(monkeypatch, [final()], limits=Limits(max_context_chars=2000))
    _, answer = agent.run_task(Task(id="context", question="x" * 3000), root, config, tracker)
    assert answer.status == "abstain"
    assert answer.reason == "task_exceeds_context_budget"
    assert calls == []


def test_task_memory_does_not_carry_over(lab, monkeypatch):
    root, tracker = lab
    config, calls = live(monkeypatch, [final()])
    agent.run_task(Task(id="first", question="first isolated marker"), root, config, tracker)
    agent.run_task(Task(id="second", question="second isolated marker"), root, config, tracker)
    assert "first isolated marker" not in json.dumps(calls[1])
    assert "second isolated marker" in json.dumps(calls[1])
