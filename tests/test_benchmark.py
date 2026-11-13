import json
from pathlib import Path

import pytest

from hacklab.benchmark import (
    Expected,
    benchmark,
    compare,
    dataset_digest,
    failures,
    load_cases,
    score_answer,
)
from hacklab.config import Config, load_config
from hacklab.models import Answer, Citation
from hacklab.practice import generate
from hacklab.tools import ToolRegistry
from hacklab.tracking import Tracker


def test_score_rejects_fabricated_quote_and_wrong_status(tmp_path):
    (tmp_path / "facts.txt").write_text("The owner is Mira.\n")
    tools = ToolRegistry(tmp_path, ["facts.txt"])
    answer = Answer(
        status="ok",
        answer="Mira",
        citations=[Citation(document="facts.txt", locator="line:1", quote="The owner is Mira.")],
    )
    expected = Expected(contains=["Mira"], citation_documents=["facts.txt"])
    assert score_answer(answer, expected, tools)["score"] == 1
    answer.citations[0].quote = "The owner is someone else."
    assert score_answer(answer, expected, tools)["score"] == 0
    answer.status = "abstain"
    assert score_answer(answer, expected, tools)["score"] == 0


def test_labels_never_part_of_task_and_digest_binds_document(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "task": {"id": "one", "question": "Who?", "documents": ["fact.txt"]},
                "expected": {"exact_answer": "secret-reference"},
            }
        )
        + "\n"
    )
    source = tmp_path / "fact.txt"
    source.write_text("owner=A")
    cases = load_cases(dataset)
    assert "secret-reference" not in cases[0].task.model_dump_json()
    before = dataset_digest(dataset, tmp_path, cases)
    source.write_text("owner=B")
    assert dataset_digest(dataset, tmp_path, cases) != before


def test_benchmark_rejects_duplicate_ids_and_escaped_documents(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    case = {"task": {"id": "one", "question": "Who?", "documents": ["../other"]}, "expected": {}}
    dataset.write_text(json.dumps(case) + "\n")
    with pytest.raises(ValueError):
        dataset_digest(dataset, tmp_path, load_cases(dataset))
    dataset.write_text((json.dumps(case) + "\n") * 2)
    with pytest.raises(ValueError, match="Duplicate"):
        load_cases(dataset)


def test_compare_unknown_cost_and_mixed_conditions():
    left = {
        "dataset_sha256": "x",
        "mode": "openai",
        "tasks": 8,
        "benchmark_kind": "local",
        "accuracy": 0.5,
        "cost_usd": None,
        "p95_elapsed_ms": 500,
    }
    right = {**left, "accuracy": 0.75, "p95_elapsed_ms": 400}
    assert compare(left, right)["right_dominates"] is None
    right["mode"] = "mock"
    with pytest.raises(ValueError, match="mode"):
        compare(left, right)


def test_numeric_fixture_rejects_number_with_matching_substring(tmp_path):
    source = tmp_path / "invoices.csv"
    source.write_text("invoice,amount\nA,200\n")
    tools = ToolRegistry(tmp_path, [source.name])
    rows = tools.execute("read", {"document": source.name})["results"]
    citation = Citation(**{key: rows[0][key] for key in ("document", "locator", "quote")})
    expected = Expected(exact_answer="200", citation_documents=[source.name])
    wrong = Answer(status="ok", answer="1200", citations=[citation])
    assert score_answer(wrong, expected, tools)["score"] == 0
    wrong.answer = "200"
    assert score_answer(wrong, expected, tools)["score"] == 1


def test_list_answers_match_as_sets_whatever_the_punctuation(tmp_path):
    tools = ToolRegistry(tmp_path, [])
    expected = Expected(answer_set=["agent-3", "agent-7", "tok-x1"])
    for text in (
        "agent-7, agent-3, tok-x1",
        "agent-3; AGENT-7 and tok-x1.",
        '["tok-x1", "agent-3", "agent-7"]',
        "- agent-3\n- agent-7\n- `tok-x1`",
    ):
        assert score_answer(Answer(status="ok", answer=text), expected, tools)["score"] == 1, text
    for text in ("agent-3, agent-7", "agent-3, agent-7, tok-x1, agent-9", ""):
        assert score_answer(Answer(status="ok", answer=text), expected, tools)["score"] == 0, text


def test_containment_needs_exactly_the_expected_actions(tmp_path):
    tools = ToolRegistry(tmp_path, [])
    wanted = [
        {"type": "quarantine_agent", "target": "agent-3"},
        {"type": "revoke_token", "target": "tok-x1"},
    ]
    expected = Expected.model_validate({"actions": wanted})

    def score(actions):
        answer = Answer.model_validate({"status": "ok", "answer": "", "actions": actions})
        return score_answer(answer, expected, tools)

    exact = score(
        [{**wanted[1], "reason": "minted"}, {"type": "Quarantine_Agent", "target": "agent-3"}]
    )
    assert exact["score"] == 1 and exact["action_precision"] == exact["action_recall"] == 1
    missing = score(wanted[:1])
    assert missing["score"] == 0 and missing["action_recall"] == 0.5
    collateral = score(wanted + [{"type": "quarantine_agent", "target": "agent-9"}])
    assert collateral["score"] == 0 and collateral["action_precision"] == pytest.approx(2 / 3)
    assert score([])["action_precision"] == 0


def test_citations_from_external_tools_are_not_checked_against_files(tmp_path):
    tools = ToolRegistry(tmp_path, [])
    answer = Answer(
        status="ok",
        answer="tok-7a",
        citations=[Citation(document="tool:list_tokens", locator="item:1", quote="token=tok-7a")],
    )
    assert score_answer(answer, Expected(exact_answer="tok-7a"), tools)["score"] == 1


def test_numeric_answers_compare_by_value_and_text_answers_by_text(tmp_path):
    tools = ToolRegistry(tmp_path, [])
    number = Expected(exact_answer="200")
    for text in ("200", "200.0", " 200.00 "):
        assert score_answer(Answer(status="ok", answer=text), number, tools)["score"] == 1
    for text in ("201", "200 EUR", "two hundred"):
        assert score_answer(Answer(status="ok", answer=text), number, tools)["score"] == 0
    label = Expected(exact_answer="agent-007")
    assert score_answer(Answer(status="ok", answer="Agent-007"), label, tools)["score"] == 1
    assert score_answer(Answer(status="ok", answer="agent-7"), label, tools)["score"] == 0


def test_failures_name_what_the_harness_itself_stopped(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("alpha\n")
    (tmp_path / "b.txt").write_text("beta\ngamma\n")
    dataset = tmp_path / "cases.jsonl"
    cases = [
        ("one", "Return it.", ["a.txt"], {"exact_answer": "alpha"}, "lookup"),
        # The offline mock abstains on two lines; a model would be asked here.
        ("two", "Return it.", ["b.txt"], {"exact_answer": "beta"}, "lookup"),
        ("three", "Return it.", ["a.txt"], {"exact_answer": "alpha"}, "late"),
    ]
    dataset.write_text(
        "".join(
            json.dumps(
                {
                    "task": {"id": name, "question": question, "documents": documents},
                    "expected": expected,
                    "category": category,
                }
            )
            + "\n"
            for name, question, documents, expected, category in cases
        )
    )
    tracker = Tracker(tmp_path / "state" / "t.sqlite")
    report = benchmark(dataset, tmp_path, Config(), tracker)
    assert [record["cause"] for record in report["records"]] == [None, None, None]
    lost = failures(report)
    assert lost["failed"] == 1 and lost["by_category"]["lookup"]["causes"] == {}
    # A missing setting shows up by name, not as a mysterious failure ...
    model = {"provider": "openai", "model": "m"}
    unset = Config.model_validate({"model": model})
    lost = failures(benchmark(dataset, tmp_path, unset, tracker))
    assert lost["by_category"]["lookup"]["causes"] == {
        "configure_small_model_base_url_and_name": ["one", "two"]
    }
    assert lost["by_category"]["late"]["statuses"] == {"error": 1}
    # ... and so does a limit that is too tight for the task.
    monkeypatch.setenv("SMALL_MODEL_API_KEY", "synthetic")
    tight = Config.model_validate(
        {
            "model": {**model, "base_url": "http://127.0.0.1:9/v1"},
            "limits": {"max_total_tokens": 128},
        }
    )
    report = benchmark(dataset, tmp_path, tight, tracker)
    lost = failures(report)
    assert lost["failed"] == 3 and report["calls"] == 0
    assert lost["by_category"]["lookup"]["causes"] == {
        "token_budget_exhausted_before_request": ["one", "two"]
    }
    assert lost["by_category"]["late"]["statuses"] == {"abstain": 1}


def test_a_parallel_benchmark_scores_the_same_in_the_same_order(tmp_path):
    out = tmp_path / "scenario"
    generate(5, out, agents=20, events=300)
    config = load_config(Path(__file__).parents[1] / "configs" / "practice.toml")
    tracker = Tracker(tmp_path / "state" / "t.sqlite")
    dataset, root = out / "cases.jsonl", out / "docs"
    alone = benchmark(dataset, root, config, tracker)
    together = benchmark(dataset, root, config, tracker, workers=4)
    keys = ("task_id", "category", "status", "score", "checks")
    assert [[r[key] for key in keys] for r in together["records"]] == [
        [r[key] for key in keys] for r in alone["records"]
    ]
    assert together["accuracy"] == alone["accuracy"] == 1.0
    assert together["dataset_sha256"] == alone["dataset_sha256"]
    with pytest.raises(ValueError, match="workers"):
        benchmark(dataset, root, config, tracker, workers=0)
