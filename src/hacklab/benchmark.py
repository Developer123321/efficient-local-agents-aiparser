"""Transparent local development scoring, independent of organizer scoring."""

import hashlib
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import Field

from hacklab.config import Config
from hacklab.models import Action, Answer, StrictModel, Task
from hacklab.toolkit import split_items
from hacklab.tools import ToolRegistry


class Expected(StrictModel):
    status: Literal["ok", "abstain", "error"] = "ok"
    exact_answer: str | None = None
    # A list answer in any order and with any separator: every item, nothing else.
    answer_set: list[str] | None = None
    contains: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    citation_documents: list[str] = Field(default_factory=list)
    # Containment: exactly these (type, target) pairs. A missing step and a
    # wrong target both fail; precision and recall are reported beside the score.
    actions: list[Action] | None = None


class Case(StrictModel):
    task: Task
    expected: Expected
    category: str = "retrieval"


def load_cases(path: Path) -> list[Case]:
    if path.stat().st_size > 8_000_000:
        raise ValueError("Benchmark dataset exceeds 8 MB")
    lines = path.read_text(encoding="utf-8").splitlines()
    cases = [Case.model_validate_json(line) for line in lines if line.strip()]
    if not cases or len(cases) > 1000:
        raise ValueError("Benchmark must contain 1..1000 cases")
    ids = [case.task.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate benchmark task IDs")
    return cases


def _items(text: str) -> set[str]:
    """The entries of a list answer, however the model punctuated it."""
    return {item.casefold() for item in split_items(text)}


def score_answer(answer: Answer, expected: Expected, tools: ToolRegistry) -> dict:
    """All declared conditions must hold. No LLM judge and no invented official score."""
    normalized = " ".join(answer.answer.casefold().split())
    conditions = {"status": answer.status == expected.status}
    detail: dict[str, float] = {}
    if expected.exact_answer is not None:
        wanted = " ".join(expected.exact_answer.casefold().split())
        try:
            # 200, 200.0 and 200.00 are the same answer to a numeric question.
            same = float(normalized) == float(wanted)
        except ValueError:
            same = normalized == wanted
        conditions["exact_answer"] = same
    if expected.answer_set is not None:
        conditions["answer_set"] = _items(answer.answer) == {
            item.casefold() for item in expected.answer_set
        }
    if expected.actions is not None:
        wanted = {(action.type.casefold(), action.target) for action in expected.actions}
        given = {(action.type.casefold(), action.target) for action in answer.actions}
        conditions["actions"] = given == wanted
        detail["action_precision"] = len(given & wanted) / len(given) if given else 0.0
        detail["action_recall"] = len(given & wanted) / len(wanted) if wanted else 1.0
    conditions["contains"] = all(s.casefold() in normalized for s in expected.contains)
    conditions["excludes"] = all(s.casefold() not in normalized for s in expected.excludes)
    conditions["required_documents"] = set(expected.citation_documents) <= {
        c.document for c in answer.citations
    }
    # Rows from an external tool are grounded by the agent's ledger, not on disk.
    conditions["grounded_quotes"] = not tools.verify_citations(
        [c for c in answer.citations if not c.document.startswith("tool:")]
    )
    return {"score": float(all(conditions.values())), "checks": conditions, **detail}


def dataset_digest(path: Path, root: Path, cases: list[Case]) -> str:
    """Bind comparison identity to reference labels AND the exact document bytes."""
    digest = hashlib.sha256(path.read_bytes())
    base = root.resolve()
    for name in sorted({name for case in cases for name in case.task.documents}):
        target = (base / name).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise ValueError("Benchmark document missing or outside the document root")
        if target.stat().st_size > 20_000_000:
            raise ValueError("Benchmark document exceeds 20 MB")
        digest.update(name.encode())
        digest.update(hashlib.sha256(target.read_bytes()).digest())
    return digest.hexdigest()


def _percentile(values: list[float], q: float) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * q) - 1)]


def benchmark(
    dataset: Path, root: Path, config: Config, tracker, repeat: int = 1, workers: int = 1
) -> dict:
    """Run every case and score it. `workers` cases run side by side.

    Parallel runs send the same requests and score the same; only the elapsed
    times differ, so compare latencies between runs of equal `workers`.
    """
    from hacklab.agent import run_task

    if not 1 <= repeat <= 10:
        raise ValueError("repeat must be 1..10")
    if not 1 <= workers <= 32:
        raise ValueError("workers must be 1..32")
    cases = load_cases(dataset)
    digest = dataset_digest(dataset, root, cases)

    def run(job: tuple[int, Case]) -> dict:
        repetition, case = job
        started = time.monotonic()
        # The runner receives ONLY Task, never Case, Expected, category or reference labels.
        # Repetitions measure variance, so each gets its own cache entries.
        run_id, answer = run_task(
            case.task, root, config, tracker, cache_salt=str(repetition) if repetition else ""
        )
        scored = score_answer(answer, case.expected, ToolRegistry(root, case.task.documents))
        tracker.finish_run(run_id, answer.status, answer.model_dump(), scored["score"])
        return {
            "run_id": run_id,
            "task_id": case.task.id,
            "category": case.category,
            "repetition": repetition,
            "status": answer.status,
            # Set when the harness, not the model, ended the task.
            "cause": answer.cause or None,
            **scored,
            "elapsed_ms": (time.monotonic() - started) * 1000,
            "usage": tracker.summary(run_id),
        }

    jobs = [(repetition, case) for repetition in range(repeat) for case in cases]
    if workers == 1:
        records = [run(job) for job in jobs]
    else:
        with ThreadPoolExecutor(workers) as pool:
            records = list(pool.map(run, jobs))  # In input order, whoever finishes first.
    costs = [r["usage"]["cost_usd"] for r in records]
    reported = [r["usage"]["reported_cost_usd"] for r in records]
    spent = [r["usage"]["spent_usd"] for r in records]
    durations = [r["elapsed_ms"] for r in records]
    return {
        "format_version": 1,
        "benchmark_kind": "local_development_not_organizer_score",
        "mode": config.model.provider,
        "dataset_sha256": digest,
        "config_sha256": hashlib.sha256(
            json.dumps(config.model_dump(), sort_keys=True).encode()
        ).hexdigest(),
        "tasks": len(records),
        "accuracy": statistics.mean(r["score"] for r in records),
        # Estimated from tokens and the configured prices, replays included:
        # what the run costs at those prices without a cache.
        "known_cost_usd": sum(r["usage"]["known_cost_usd"] for r in records),
        "cost_usd": None if None in costs else sum(costs),
        "unknown_cost_calls": sum(r["usage"]["unknown_cost_calls"] for r in records),
        # As the provider reported it reply by reply, replays with the figure
        # they had when live; None unless every call carries one.
        "reported_cost_usd": None if None in reported else sum(reported),
        "reported_cost_calls": sum(r["usage"]["reported_cost_calls"] for r in records),
        # What this run itself paid: live calls only, reported where possible.
        "spent_usd": None if None in spent else sum(spent),
        "calls": sum(r["usage"]["calls"] for r in records),
        # Replayed from the development cache: measured once, not paid again.
        "cache_hits": sum(r["usage"]["cache_hits"] for r in records),
        # Tokens stay comparable while the event's prices are still unknown.
        "known_input_tokens": sum(r["usage"]["known_input_tokens"] for r in records),
        "known_output_tokens": sum(r["usage"]["known_output_tokens"] for r in records),
        "usage_complete": all(r["usage"]["usage_complete"] for r in records),
        "p50_elapsed_ms": statistics.median(durations),
        "p95_elapsed_ms": _percentile(durations, 0.95),
        "by_category": {
            category: statistics.mean(r["score"] for r in records if r["category"] == category)
            for category in sorted({r["category"] for r in records})
        },
        "records": records,
    }


def compare(left: dict, right: dict) -> dict:
    for key in ("dataset_sha256", "mode", "tasks", "benchmark_kind"):
        if left[key] != right[key]:
            raise ValueError(f"Incomparable benchmark files: different {key}")
    # The provider's own figures where both runs have them for every call,
    # else the estimates from the configured prices; never one against the other.
    basis = next(
        (
            name
            for name, key in (("reported", "reported_cost_usd"), ("estimated", "cost_usd"))
            if left.get(key) is not None and right.get(key) is not None
        ),
        None,
    )
    costs_known = basis is not None
    key = "reported_cost_usd" if basis == "reported" else "cost_usd"
    accuracy_delta = right["accuracy"] - left["accuracy"]
    cost_delta = right[key] - left[key] if costs_known else None
    tokens = ("known_input_tokens", "known_output_tokens", "calls")
    return {
        "accuracy_delta": accuracy_delta,
        "cost_delta_usd": cost_delta,
        "cost_basis": basis,
        **{
            f"{key}_delta": right[key] - left[key] for key in tokens if key in left and key in right
        },
        "p95_delta_ms": right["p95_elapsed_ms"] - left["p95_elapsed_ms"],
        "right_dominates": (
            accuracy_delta >= 0 and cost_delta <= 0 and (accuracy_delta > 0 or cost_delta < 0)
            if costs_known
            else None
        ),
        "note": "No combined official score: organizer normalization is not yet known.",
    }


def failures(report: dict) -> dict:
    """Where a run lost points: per category, which check failed for which task.

    A failed `required_documents` or `grounded_quotes` points at retrieval, a
    failed answer check with sources present at the model or a rule, and a
    status mismatch at the abstention policy or a provider error. `causes`
    names what the harness itself stopped: a limit in `[limits]` that is too
    tight, a provider failure, a setup error. Fix those before any prompt.
    """
    grouped: dict[str, dict] = {}
    for record in report["records"]:
        entry = grouped.setdefault(
            record["category"],
            {"tasks": 0, "failed": 0, "checks": {}, "statuses": {}, "causes": {}},
        )
        entry["tasks"] += 1
        if record["score"] >= 1:
            continue
        entry["failed"] += 1
        entry["statuses"][record["status"]] = entry["statuses"].get(record["status"], 0) + 1
        if record.get("cause"):
            entry["causes"].setdefault(record["cause"], []).append(record["task_id"])
        for check, passed in record["checks"].items():
            if not passed:
                entry["checks"].setdefault(check, []).append(record["task_id"])
    return {
        "tasks": report["tasks"],
        "failed": sum(entry["failed"] for entry in grouped.values()),
        "calls_per_task": report["calls"] / report["tasks"],
        "by_category": dict(sorted(grouped.items())),
    }
