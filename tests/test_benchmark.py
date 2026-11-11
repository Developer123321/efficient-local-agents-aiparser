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
