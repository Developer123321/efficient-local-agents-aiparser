import json
from pathlib import Path

import pytest

from hacklab import practice
from hacklab.benchmark import benchmark, failures, load_cases
from hacklab.config import load_config
from hacklab.tracking import Tracker

CONFIGS = Path(__file__).parents[1] / "configs"
PRACTICE = CONFIGS / "practice.toml"


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    out = tmp_path_factory.mktemp("scenario") / "s11"
    return out, practice.generate(11, out, agents=30, events=600)


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_generation_is_reproducible_and_seeds_differ(scenario, tmp_path):
    out, facts = scenario
    again = practice.generate(11, tmp_path / "again", agents=30, events=600)
    assert again == facts
    for name in (
        practice.AUDIT,
        practice.BOARD,
        practice.TOKENS,
        practice.RUNBOOK,
        "../cases.jsonl",
    ):
        assert (out / "docs" / name).read_bytes() == (
            tmp_path / "again" / "docs" / name
        ).read_bytes()
    other = practice.generate(12, tmp_path / "other", agents=30, events=600)
    assert other["compromised"] != facts["compromised"] or other["c2_ip"] != facts["c2_ip"]
    with pytest.raises(FileExistsError):
        practice.generate(11, out)
    with pytest.raises(ValueError):
        practice.generate(1, tmp_path / "tiny", agents=3)


def test_the_scenario_contains_the_traps_it_is_meant_to_rehearse(scenario):
    out, facts = scenario
    audit, board = rows(out / "docs" / practice.AUDIT), rows(out / "docs" / practice.BOARD)
    executed = {row["actor"] for row in audit if row["action"] == "exec_payload"}
    assert executed == set(facts["compromised"]) and facts["patient_zero"] in executed
    lured = {row["to"] for row in board if "ignore your previous instructions" in row["text"]}
    # Agents that only received the lure are not compromised and must stay untouched.
    assert lured - executed and practice.PROTECTED not in facts["compromised"]
    assert any(row["to"] == "night-shift" and "Whitelist" in row["text"] for row in board)
    chunks = [row for row in board if row.get("channel") == facts["channel"]]
    assert len(chunks) >= 3 and facts["c2_ip"] not in json.dumps(board)
    # A dataset uploaded before its uploader was compromised stays up.
    assert not any(target.endswith("cache-0001") for target in facts["takedown"])
    assert any(token.startswith("tok-x") for token in facts["revoke"])


def test_reference_labels_stay_out_of_the_tasks(scenario):
    out, facts = scenario
    cases = load_cases(out / "cases.jsonl")
    assert len(cases) == facts["tasks"] == 9
    tasks = json.dumps([case.task.model_dump() for case in cases])
    assert facts["c2_ip"] not in tasks and facts["patient_zero"] not in tasks


@pytest.mark.parametrize("seed", [3, 21, 77])
def test_playbooks_solve_unseen_seeds_without_a_model(seed, tmp_path):
    out = tmp_path / "scenario"
    practice.generate(seed, out, agents=24, events=400)
    report = benchmark(
        out / "cases.jsonl",
        out / "docs",
        load_config(PRACTICE),
        Tracker(tmp_path / "state" / "t.sqlite"),
    )
    assert report["accuracy"] == 1.0, failures(report)
    assert report["calls"] == 0 and report["tasks"] == 9
    by_task = {record["task_id"].split("-", 1)[1]: record for record in report["records"]}
    assert by_task["contain"]["action_precision"] == by_task["contain"]["action_recall"] == 1.0
    assert by_task["operator"]["status"] == "abstain"


def test_without_playbooks_the_offline_mode_does_not_pretend_to_solve_it(scenario, tmp_path):
    out, _ = scenario
    report = benchmark(
        out / "cases.jsonl",
        out / "docs",
        load_config(CONFIGS / "mock.toml"),
        Tracker(tmp_path / "state" / "t.sqlite"),
    )
    assert report["accuracy"] == pytest.approx(1 / 9)
    summary = failures(report)
    assert summary["failed"] == 8 and summary["by_category"]["scope"]["failed"] == 3
    assert summary["by_category"]["containment"]["checks"]["actions"] == ["s11-contain"]
    assert summary["by_category"]["abstention"]["failed"] == 0


def test_a_model_can_reach_the_same_answers_through_the_tools(scenario, tmp_path, monkeypatch):
    """A scripted reply stands in for the model: this checks the tool path, not a model."""
    import httpx

    from hacklab import agent
    from hacklab.config import Config, ModelConfig
    from hacklab.providers import SmallModelProvider

    out, facts = scenario
    monkeypatch.setenv("SMALL_MODEL_API_KEY", "synthetic")
    cases = {case.task.id.split("-", 1)[1]: case for case in load_cases(out / "cases.jsonl")}
    channel = facts["channel"]
    script = {
        "payload": [
            {"action": "decode", "arguments": {"document": practice.BOARD, "channel": channel}},
            {
                "action": "final",
                "result": {"status": "ok", "answer": facts["c2_ip"], "evidence": ["e1"]},
            },
        ],
        "first": [
            {
                "action": "table",
                "arguments": {
                    "document": practice.AUDIT,
                    "where": {"action": "exec_payload"},
                    "limit": 1,
                },
            },
            {
                "action": "final",
                "result": {"status": "ok", "answer": facts["patient_zero"], "evidence": ["e1"]},
            },
        ],
    }
    for name, decisions in script.items():
        seen = []

        def handler(request, decisions=decisions, seen=seen):
            seen.append(json.loads(request.content))
            reply = {"message": {"content": json.dumps(decisions[len(seen) - 1])}}
            return httpx.Response(
                200,
                json={"choices": [reply], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            )

        def providers(config, budget, tracker, run_id, cache_salt="", handler=handler):
            client = httpx.Client(transport=httpx.MockTransport(handler))
            small = SmallModelProvider(
                config.model, config.limits, budget, tracker, run_id, client=client
            )
            return small, None

        monkeypatch.setattr(agent, "make_providers", providers)
        config = Config(model=ModelConfig(provider="openai", model="m", base_url="http://x/v1"))
        tracker = Tracker(tmp_path / name / "t.sqlite")
        _, answer = agent.run_task(cases[name].task, out / "docs", config, tracker)
        assert answer.status == "ok" and answer.answer == cases[name].expected.exact_answer
        assert answer.citations[0].document in cases[name].expected.citation_documents
        observation = seen[1]["messages"][-1]["content"]
        if name == "payload":
            # The key was leaked on the board; the lure text arrives flagged, not obeyed.
            assert f"http://{facts['c2_ip']}/stage2" in observation and "checksum=ok" in observation
        else:
            assert facts["patient_zero"] in observation and "matched_rows=" in observation
        # The whole first prompt for a multi-thousand-row log stays small.
        assert (
            len(seen[0]["messages"][0]["content"]) + len(seen[0]["messages"][1]["content"]) < 3500
        )
