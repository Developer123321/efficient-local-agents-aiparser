import json
import signal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from hacklab import optimize
from hacklab.config import Config, DevBudget
from hacklab.optimize import (
    ConfigEdit,
    MeasurementUncertain,
    apply_edit,
    review,
    run_campaign,
    safe_config,
    safe_report,
)
from hacklab.tracking import Tracker

DIGEST = "a" * 64
HEAD = "b" * 40


def report(accuracy=0.8, cost=0.02, *, unknown=False):
    return {
        "dataset_sha256": DIGEST,
        "config_sha256": "c" * 64,
        "mode": "mock",
        "benchmark_kind": "local_development_not_organizer_score",
        "tasks": 10,
        "accuracy": accuracy,
        "cost_usd": cost,
        "known_cost_usd": cost or 0.0,
        "unknown_cost_calls": int(unknown),
        "usage_complete": not unknown,
        "calls": 1,
        "cache_hits": 0,
        "known_input_tokens": 100,
        "known_output_tokens": 10,
        "p50_elapsed_ms": 1.0,
        "p95_elapsed_ms": 2.0,
        "records": [
            {
                "task_id": "PRIVATE_TASK",
                "expected": "REFERENCE_ANSWER",
                "answer": "MODEL_BODY",
                "checks": {"exact_answer": False, "PRIVATE_CHECK": False},
            }
        ],
        "unrecognized": "PRIVATE_DOCUMENT",
    }


def rows(tracker):
    with tracker._db() as db:
        return [dict(row) for row in db.execute("SELECT * FROM optimization_trials ORDER BY id")]


def campaign(config, tracker, runner, proposer, **kwargs):
    return run_campaign(
        config, tracker, runner, proposer, git_head=HEAD, dev_digest=DIGEST, **kwargs
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("guard.injection", "off"),
        ("model.provider", "other"),
        ("budget.max_usd", 100),
        ("agent.inventory", "false"),
        ("agent.inventory", 0),
        ("agent.list_preview", True),
        ("agent.preload_results", 11),
        ("limits.max_observation_chars", 499),
        ("agent.workflow", "swarm"),
    ],
)
def test_optimizer_edit_rejects_protected_fields_and_wrong_types(field, value):
    with pytest.raises(ValidationError):
        ConfigEdit(field=field, value=value, hypothesis="reduce_context")


def test_one_edit_preserves_model_guards_and_all_other_parameters():
    original = Config()
    edit = ConfigEdit(field="limits.max_observation_chars", value=500, hypothesis="reduce_context")
    changed = apply_edit(original, edit)
    expected = original.model_dump()
    expected["limits"]["max_observation_chars"] = 500
    assert changed.model_dump() == expected
    with pytest.raises(ValueError, match="setting_unchanged"):
        apply_edit(changed, edit)


@pytest.mark.parametrize(
    "accuracy,cost,accepted,reason",
    [
        (0.9, 0.02, True, "pareto_improvement"),
        (0.8, 0.01, True, "pareto_improvement"),
        (0.9, 0.03, False, "tradeoff_rejected"),
        (0.7, 0.01, False, "tradeoff_rejected"),
        (0.8, 0.02, False, "no_strict_gain"),
        (0.9, None, False, "unknown_cost"),
    ],
)
def test_deterministic_reviewer_never_forces_improvement(accuracy, cost, accepted, reason):
    assert review(safe_report(report()), safe_report(report(accuracy, cost))) == (accepted, reason)


def test_aggregates_and_safe_config_exclude_bodies_labels_and_endpoints():
    aggregate = safe_report(report())
    assert aggregate["failed_checks"]["exact_answer"] == 1
    text = json.dumps(aggregate)
    assert all(
        secret not in text
        for secret in (
            "PRIVATE_TASK",
            "REFERENCE_ANSWER",
            "MODEL_BODY",
            "PRIVATE_CHECK",
            "PRIVATE_DOCUMENT",
        )
    )
    config = Config()
    config.model.base_url = "https://user:private-password@example.invalid/api?key=private-key"
    safe = safe_config(config)
    assert safe["agent"]["list_preview"] == config.agent.list_preview
    assert safe["limits"]["max_observation_chars"] == config.limits.max_observation_chars
    assert "private-" not in json.dumps(safe) and "example.invalid" not in json.dumps(safe)


def test_campaign_measures_each_edit_and_only_promotes_pareto_improvement(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")
    measured_configs, analyst_inputs = [], []
    results = iter([report(), report(0.9, 0.015), report(0.85, 0.01)])
    edits = iter(
        [
            ConfigEdit(field="agent.inventory", value=False, hypothesis="adjust_inventory"),
            ConfigEdit(
                field="limits.max_observation_chars", value=500, hypothesis="reduce_context"
            ),
        ]
    )

    def runner(config, remaining):
        assert remaining > 0
        measured_configs.append(config.model_dump())
        return next(results)

    def proposer(config, aggregate, recent, remaining):
        analyst_inputs.append(json.dumps(aggregate))
        return next(edits)

    result = campaign(Config(), tracker, runner, proposer, max_iterations=2)
    assert result["status"] == "completed"
    assert result["champion_report"]["accuracy"] == 0.9
    assert result["champion_config"]["agent"]["inventory"] is False
    assert result["champion_config"]["limits"]["max_observation_chars"] == 4000
    assert len(measured_configs) == 3
    assert [row["status"] for row in rows(tracker)] == ["baseline", "accepted", "rejected"]
    assert all(
        "REFERENCE_ANSWER" not in text and "MODEL_BODY" not in text for text in analyst_inputs
    )


def test_unknown_baseline_cost_stops_before_any_proposal(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")

    def no_proposal(*args):
        pytest.fail("unknown cost must stop the next call")

    result = campaign(
        Config(),
        tracker,
        lambda *_: report(cost=None, unknown=True),
        no_proposal,
    )
    assert result["status"] == "unknown_cost"
    assert json.loads(rows(tracker)[0]["report_json"])["cost_usd"] is None


def test_lower_existing_shared_budget_is_preserved_and_stops_next_calls(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")

    def receipt(tokens):
        run_id = tracker.start_run("receipt", {})
        tracker.record_call(
            run_id,
            "openai",
            "synthetic",
            tokens,
            0,
            1.0,
            "ok",
            input_rate=1.0,
            output_rate=0.0,
        )
        tracker.finish_run(run_id, "ok")

    receipt(9000)

    def runner(config, remaining):
        assert config.budget.max_usd == 0.01
        receipt(2000)
        return report(cost=0.002)

    def forbidden(*args):
        pytest.fail("the existing shared ceiling must stop the proposer")

    result = campaign(
        Config(budget=DevBudget(max_usd=0.01)),
        tracker,
        runner,
        forbidden,
        max_usd=0.05,
    )
    assert result["status"] == "budget_exhausted"
    assert result["spent_usd"] == pytest.approx(0.002)


def test_proposal_failure_persists_fixed_error_without_raw_exception(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")

    def broken(*args):
        raise RuntimeError("PRIVATE_MODEL_BODY")

    result = campaign(Config(), tracker, lambda *_: report(), broken, max_iterations=1)
    assert result["status"] == "completed"
    failure = rows(tracker)[-1]
    assert failure["status"] == "error"
    assert "PRIVATE_MODEL_BODY" not in json.dumps(failure)
    assert json.loads(failure["decision_json"])["reason"] == "proposal_or_measurement_failed"


def test_measurement_timeout_is_persisted(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")

    def timeout(*args):
        raise TimeoutError("private timeout body")

    result = campaign(Config(), tracker, timeout, lambda *_: None)
    assert result["status"] == "timed_out"
    assert rows(tracker)[0]["status"] == "timed_out"


def test_killed_measurement_marks_unknown_spend_without_inventing_requests(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")

    def uncertain(*args):
        raise MeasurementUncertain("unobserved_external_attempt_possible")

    result = campaign(Config(), tracker, uncertain, lambda *_: pytest.fail("must stop"))
    assert result["status"] == "unknown_cost" and result["spend_complete"] is False
    assert tracker.spend()["calls"] == 0
    trial = rows(tracker)[0]
    assert trial["status"] == "timed_out"
    assert json.loads(trial["decision_json"])["unobserved_external_attempt_possible"] is True
    assert json.loads(trial["report_json"])["cost_usd"] is None


def test_champion_resume_and_frozen_holdout_never_feed_back_into_tuning(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")
    measured = []

    def baseline(*args):
        measured.append("development")
        return report()

    def forbidden(*args):
        pytest.fail("a resumed frozen candidate must not tune or rerun its baseline")

    initial = campaign(Config(), tracker, baseline, forbidden, max_iterations=0)
    assert initial["status"] == "completed"

    def holdout(*args):
        measured.append("holdout")
        held_out = report(accuracy=0.1)
        held_out["dataset_sha256"] = "d" * 64
        return held_out

    frozen = campaign(
        Config(),
        tracker,
        forbidden,
        forbidden,
        finalize=True,
        holdout_runner=holdout,
        holdout_digest=lambda: "d" * 64,
    )
    assert frozen["status"] == "finalized"
    assert frozen["champion_report"]["accuracy"] == 0.8
    assert measured == ["development", "holdout"]
    repeated = campaign(Config(), tracker, forbidden, forbidden)
    assert repeated["status"] == "already_finalized"
    repeated_again = campaign(Config(), tracker, forbidden, forbidden)
    assert repeated_again["status"] == "already_finalized"
    assert measured == ["development", "holdout"]


@pytest.mark.parametrize("failure", [RuntimeError("failed"), MeasurementUncertain("timed out")])
def test_failed_holdout_still_freezes_context_before_attempt(tmp_path, failure):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")
    campaign(Config(), tracker, lambda *_: report(), lambda *_: None, max_iterations=0)
    attempts = []

    def holdout(*args):
        attempts.append("holdout")
        raise failure

    result = campaign(
        Config(),
        tracker,
        lambda *_: pytest.fail("no development rerun"),
        lambda *_: pytest.fail("no proposal"),
        finalize=True,
        holdout_runner=holdout,
        holdout_digest=lambda: "d" * 64,
    )
    assert result["status"] in {"error", "unknown_cost"}
    assert json.loads(rows(tracker)[-1]["decision_json"])["phase"] == "holdout"
    for finalize in (False, True):
        later = campaign(
            Config(),
            tracker,
            lambda *_: pytest.fail("must remain frozen"),
            lambda *_: pytest.fail("must remain frozen"),
            finalize=finalize,
            holdout_runner=holdout,
        )
        assert later["status"] == "already_finalized"
    assert attempts == ["holdout"]


def test_holdout_digest_must_differ_before_any_holdout_measurement(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")
    campaign(Config(), tracker, lambda *_: report(), lambda *_: None, max_iterations=0)
    result = campaign(
        Config(),
        tracker,
        lambda *_: pytest.fail("frozen baseline"),
        lambda *_: None,
        finalize=True,
        holdout_runner=lambda *_: pytest.fail("must reject before model calls"),
        holdout_digest=lambda: DIGEST,
    )
    assert result["status"] == "error"
    assert json.loads(rows(tracker)[-1]["decision_json"])["reason"] == "holdout_not_distinct"
    later = campaign(Config(), tracker, lambda *_: pytest.fail("still frozen"), lambda *_: None)
    assert later["status"] == "already_finalized"


def test_holdout_digest_is_claimed_globally_across_source_revisions(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")
    evaluated = []

    def at_head(head, **kwargs):
        return run_campaign(
            Config(),
            tracker,
            lambda *_: report(),
            lambda *_: None,
            git_head=head,
            dev_digest=DIGEST,
            max_iterations=0,
            **kwargs,
        )

    def holdout(digest):
        def run(*args):
            evaluated.append(digest)
            measured = report()
            measured["dataset_sha256"] = digest
            return measured

        return run

    first_digest = "d" * 64
    at_head(HEAD)
    first = at_head(
        HEAD,
        finalize=True,
        holdout_runner=holdout(first_digest),
        holdout_digest=lambda: first_digest,
    )
    assert first["status"] == "finalized"
    next_head = "e" * 40
    at_head(next_head)
    reused = at_head(
        next_head,
        finalize=True,
        holdout_runner=lambda *_: pytest.fail("claimed holdout must never run again"),
        holdout_digest=lambda: first_digest,
    )
    assert reused["status"] == "holdout_already_used"
    assert json.loads(rows(tracker)[-1]["decision_json"])["reason"] == "holdout_already_used"
    assert evaluated == [first_digest]
    # A genuinely different held-out corpus remains available under another
    # source context; a refused reuse cannot reopen its already frozen context.
    new_digest, new_head = "e" * 64, "f" * 40
    at_head(new_head)
    fresh = at_head(
        new_head,
        finalize=True,
        holdout_runner=holdout(new_digest),
        holdout_digest=lambda: new_digest,
    )
    assert fresh["status"] == "finalized"
    assert evaluated == [first_digest, new_digest]
    with tracker._db() as db:
        assert db.execute("SELECT COUNT(*) FROM optimization_holdouts").fetchone()[0] == 2


def test_reserved_holdout_cannot_become_development_under_another_source_revision(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")
    campaign(Config(), tracker, lambda *_: report(), lambda *_: None, max_iterations=0)
    reserved = "d" * 64
    held_out = report()
    held_out["dataset_sha256"] = reserved
    finalized = campaign(
        Config(),
        tracker,
        lambda *_: pytest.fail("must not rerun the development baseline"),
        lambda *_: pytest.fail("must not propose during finalization"),
        finalize=True,
        holdout_runner=lambda *_: held_out,
        holdout_digest=lambda: reserved,
    )
    assert finalized["status"] == "finalized"
    result = run_campaign(
        Config(),
        tracker,
        lambda *_: pytest.fail("reserved hold-out must not be measured as development"),
        lambda *_: pytest.fail("reserved hold-out must never reach the proposer"),
        git_head="e" * 40,
        dev_digest=reserved,
    )
    assert result["status"] == "development_is_reserved_holdout"
    refused = rows(tracker)[-1]
    assert refused["status"] == "rejected" and json.loads(refused["report_json"]) == {}
    assert json.loads(refused["decision_json"])["reason"] == "development_is_reserved_holdout"
    assert tracker.spend()["calls"] == 0


@pytest.mark.parametrize("failure", ["nonzero", "missing", "invalid-json", "non-object"])
def test_measurement_child_failure_stops_worker_with_uncertainty_without_extra_calls(
    tmp_path, monkeypatch, failure
):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")
    args = SimpleNamespace(
        dataset=tmp_path / "dev.jsonl",
        root=tmp_path / "documents",
        state=tmp_path / "state" / "optimization",
        config=tmp_path / "config.toml",
        db=tracker.path,
        env_file=tmp_path / ".env",
        repeat=1,
        finalize=False,
        holdout_dataset=None,
        holdout_root=None,
        iterations=3,
        max_usd=0.05,
        max_seconds=60,
    )
    monkeypatch.setenv("HACKLAB_OPTIMIZATION_UNKNOWN_BASELINE", "0")
    monkeypatch.delenv("HACKLAB_OPTIMIZATION_DEADLINE", raising=False)
    monkeypatch.setattr(optimize, "load_cases", lambda *_: [])
    monkeypatch.setattr(optimize, "dataset_digest", lambda *_: DIGEST)
    proposals, commands = [], []

    def proposer(*_):
        proposals.append("proposal")
        return ConfigEdit(field="agent.inventory", value=False, hypothesis="adjust_inventory")

    def command(argv, *_):
        assert "--_measure" in argv
        commands.append(argv)
        output = optimize.Path(argv[argv.index("--_out") + 1])
        if len(commands) == 1:
            output.write_text(json.dumps(report()))
        elif failure == "nonzero":
            # _command converts a negative child return code such as SIGKILL
            # into RuntimeError. No provider receipt is established by it.
            raise RuntimeError("PRIVATE_CHILD_ERROR: owned_child_failed -9")
        elif failure == "invalid-json":
            output.write_text("{not JSON")
        elif failure == "non-object":
            output.write_text("[]")
        # A successful child with missing output is just as inconclusive.

    monkeypatch.setattr(optimize, "ModelProposer", lambda *_: proposer)
    monkeypatch.setattr(optimize, "_command", command)
    result = optimize._worker(args, Config(), tracker, HEAD)
    assert result["status"] == "unknown_cost" and result["spend_complete"] is False
    assert len(proposals) == 1 and len(commands) == 2
    assert tracker.spend()["calls"] == 0
    failed = rows(tracker)[-1]
    assert failed["status"] == "error"
    decision = json.loads(failed["decision_json"])
    assert decision["reason"] == "measurement_incomplete"
    assert decision["unobserved_external_attempt_possible"] is True
    assert json.loads(failed["report_json"])["cost_usd"] is None
    assert "PRIVATE_CHILD_ERROR" not in json.dumps(rows(tracker))


def test_tuning_resume_reuses_configuration_but_remeasures_fresh_baseline(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")
    results = iter([report(), report(0.9, 0.01)])
    initial = campaign(
        Config(),
        tracker,
        lambda *_: next(results),
        lambda *_: ConfigEdit(field="agent.inventory", value=False, hypothesis="adjust_inventory"),
        max_iterations=1,
    )
    assert initial["champion_config"]["agent"]["inventory"] is False
    measured = []

    def fresh_baseline(config, remaining):
        measured.append(config.agent.inventory)
        return report(0.7, 0.025)

    resumed = campaign(Config(), tracker, fresh_baseline, lambda *_: None, max_iterations=0)
    assert measured == [False]
    assert resumed["champion_report"]["accuracy"] == 0.7
    assert json.loads(rows(tracker)[-1]["decision_json"])["reason"] == "measured_baseline"


def test_prior_rejected_edits_reach_analyst_and_duplicate_is_not_measured(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")
    results = iter([report(), report(0.7, 0.01)])
    edit = ConfigEdit(field="agent.inventory", value=False, hypothesis="adjust_inventory")
    campaign(Config(), tracker, lambda *_: next(results), lambda *_: edit, max_iterations=1)
    measured, histories = [], []

    def runner(config, remaining):
        measured.append(config.agent.inventory)
        return report(0.82, 0.02)

    def proposer(config, aggregate, recent, remaining):
        histories.append(recent.copy())
        return edit

    resumed = campaign(Config(), tracker, runner, proposer, max_iterations=2)
    assert resumed["status"] == "completed" and measured == [True]
    assert histories[0][0]["decision"]["reason"] == "tradeoff_rejected"
    assert histories[0][0]["decision"]["field"] == "agent.inventory"
    assert histories[0][0]["measured"]["accuracy"] == 0.7
    assert [json.loads(row["decision_json"])["reason"] for row in rows(tracker)[-2:]] == [
        "duplicate_candidate",
        "duplicate_candidate",
    ]


def test_command_interrupt_cleans_only_its_registered_process_group(tmp_path, monkeypatch):
    killed = []

    class Child:
        pid = 12345
        waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt
            return 0

    child = Child()
    monkeypatch.setattr(optimize.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(optimize.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(KeyboardInterrupt):
        optimize._command(["owned-fixture"], tmp_path, {}, 5)
    assert killed == [(child.pid, signal.SIGTERM), (child.pid, signal.SIGKILL)]
    assert not optimize._OWNED_PROCESSES


def test_worker_signal_cleans_registered_nested_groups_before_exiting(monkeypatch):
    killed = []

    class Child:
        def __init__(self, pid):
            self.pid = pid

        def wait(self, timeout=None):
            return 0

    optimize._OWNED_PROCESSES.update({Child(12345): 2, Child(12346): 2})
    monkeypatch.setattr(optimize.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(SystemExit) as interrupted:
        optimize._interrupted(signal.SIGTERM, None)
    assert interrupted.value.code == 128 + signal.SIGTERM
    assert {pid for pid, _ in killed} == {12345, 12346}
    assert not optimize._OWNED_PROCESSES


def test_interrupted_baseline_persists_uncertainty_and_reraises(tmp_path):
    tracker = Tracker(tmp_path / "state" / "tracking.sqlite")

    def interrupted(*args):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        campaign(Config(), tracker, interrupted, lambda *_: None)
    trial = rows(tracker)[0]
    assert trial["status"] == "error"
    assert json.loads(trial["decision_json"])["reason"] == "campaign_interrupted"
    with tracker._db() as db:
        assert (
            db.execute("SELECT status FROM optimization_campaigns").fetchone()[0] == "unknown_cost"
        )


@pytest.mark.parametrize("precheck_failure", [False, True])
def test_cli_checks_committed_worktree_before_starting_optimizer(
    tmp_path, monkeypatch, capsys, precheck_failure
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    config = checkout / "config.toml"
    config.write_text("uncommitted configuration must not be copied")
    commands = []

    def git(repo, *arguments):
        if arguments == ("rev-parse", "--show-toplevel"):
            return str(checkout)
        if arguments == ("rev-parse", "HEAD"):
            return HEAD
        if arguments[:3] == ("worktree", "add", "--detach"):
            optimize.Path(arguments[3]).mkdir()
        return ""

    def command(argv, cwd, env, timeout, **kwargs):
        assert timeout > 0
        commands.append(argv)
        if argv[:2] == ["bash", "scripts/check"]:
            assert cwd != checkout
            if precheck_failure:
                raise RuntimeError("precheck failed before any measurement child")
            return
        assert commands[0] == ["bash", "scripts/check"]
        assert argv[argv.index("--config") + 1] == str(cwd / "config.toml")
        assert argv[argv.index("--repeat") + 1] == "2"
        optimize.Path(argv[argv.index("--_out") + 1]).write_text('{"status":"completed"}')

    monkeypatch.setattr(optimize, "_git", git)
    monkeypatch.setattr(optimize, "_command", command)
    assert optimize.main(
        [
            "--config",
            str(config),
            "--dataset",
            str(tmp_path / "dev.jsonl"),
            "--root",
            str(tmp_path / "docs"),
            "--state",
            str(tmp_path / "state"),
            "--db",
            str(tmp_path / "state" / "tracking.sqlite"),
        ]
    ) == int(precheck_failure)
    output = capsys.readouterr().out
    if precheck_failure:
        assert len(commands) == 1
        assert json.loads(output)["reason"] == "optimizer_setup_or_child_failed"
        assert "unobserved_external_attempt_possible" not in output
        assert not (tmp_path / "state" / "tracking.sqlite").exists()
    else:
        assert len(commands) == 2
        assert "completed" in output


def test_cli_rejects_unsupported_process_group_platform_before_any_worktree(monkeypatch, capsys):
    monkeypatch.delattr(optimize.os, "killpg", raising=False)
    monkeypatch.setattr(
        optimize, "_git", lambda *_: pytest.fail("must not create or inspect worktrees")
    )
    assert (
        optimize.main(
            [
                "--config",
                "config.toml",
                "--dataset",
                "dev.jsonl",
                "--root",
                "documents",
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["reason"] == "unsupported_platform"
