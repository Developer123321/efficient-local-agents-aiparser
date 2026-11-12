import json

import pytest

from hacklab.benchmark import Expected, compare, dataset_digest, load_cases, score_answer
from hacklab.models import Answer, Citation
from hacklab.tools import ToolRegistry


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
