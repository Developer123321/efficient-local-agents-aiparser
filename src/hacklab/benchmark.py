"""Transparent local development scoring, independent of organizer scoring."""

import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from typing import Literal

from pydantic import Field

from hacklab.config import Config
from hacklab.models import Answer, StrictModel, Task
from hacklab.tools import ToolRegistry


class Expected(StrictModel):
    status: Literal["ok", "abstain", "error"] = "ok"
    exact_answer: str | None = None
    contains: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    citation_documents: list[str] = Field(default_factory=list)


class Case(StrictModel):
    task: Task
    expected: Expected
    category: str = "retrieval"


def load_cases(path: Path) -> list[Case]:
    if path.stat().st_size > 8_000_000:
        raise ValueError("Benchmark dataset exceeds 8 MB")
    cases = [
        Case.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()
    ]
    if not cases or len(cases) > 1000:
        raise ValueError("Benchmark must contain 1..1000 cases")
    ids = [case.task.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate benchmark task IDs")
    return cases


def score_answer(answer: Answer, expected: Expected, tools: ToolRegistry) -> dict:
    """All declared conditions must hold. No LLM judge and no invented official score."""
    normalized = " ".join(answer.answer.casefold().split())
    conditions = {"status": answer.status == expected.status}
    if expected.exact_answer is not None:
        conditions["exact_answer"] = normalized == " ".join(
            expected.exact_answer.casefold().split()
        )
    conditions["contains"] = all(s.casefold() in normalized for s in expected.contains)
    conditions["excludes"] = all(s.casefold() not in normalized for s in expected.excludes)
    conditions["required_documents"] = set(expected.citation_documents) <= {
        c.document for c in answer.citations
    }
    conditions["grounded_quotes"] = not tools.verify_citations(answer.citations)
    return {"score": float(all(conditions.values())), "checks": conditions}


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


def benchmark(dataset: Path, root: Path, config: Config, tracker, repeat: int = 1) -> dict:
    from hacklab.agent import run_task

    if not 1 <= repeat <= 10:
        raise ValueError("repeat must be 1..10")
    cases = load_cases(dataset)
    digest = dataset_digest(dataset, root, cases)
    records = []
    for repetition in range(repeat):
        for case in cases:
            started = time.monotonic()
            # The runner receives ONLY Task, never Case, Expected, category or reference labels.
            run_id, answer = run_task(case.task, root, config, tracker)
            scored = score_answer(answer, case.expected, ToolRegistry(root, case.task.documents))
            tracker.finish_run(run_id, answer.status, answer.model_dump(), scored["score"])
            records.append(
                {
                    "run_id": run_id,
                    "task_id": case.task.id,
                    "category": case.category,
                    "repetition": repetition,
                    "status": answer.status,
                    **scored,
                    "elapsed_ms": (time.monotonic() - started) * 1000,
                    "usage": tracker.summary(run_id),
                }
            )
    costs = [r["usage"]["cost_usd"] for r in records]
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
        "known_cost_usd": sum(r["usage"]["known_cost_usd"] for r in records),
        "cost_usd": None if None in costs else sum(costs),
        "unknown_cost_calls": sum(r["usage"]["unknown_cost_calls"] for r in records),
        "calls": sum(r["usage"]["calls"] for r in records),
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
    costs_known = left["cost_usd"] is not None and right["cost_usd"] is not None
    accuracy_delta = right["accuracy"] - left["accuracy"]
    cost_delta = right["cost_usd"] - left["cost_usd"] if costs_known else None
    tokens = ("known_input_tokens", "known_output_tokens", "calls")
    return {
        "accuracy_delta": accuracy_delta,
        "cost_delta_usd": cost_delta,
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
