"""Local CLI. No network calls are made unless a live task is explicitly run."""

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from hacklab.config import Config, load_config, read_secret
from hacklab.models import Answer, Task
from hacklab.tracking import Tracker


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def open_tracker(path: Path) -> Tracker:
    """Receipts must never stop a run: fall back to a temporary database."""
    try:
        return Tracker(path)
    except (OSError, sqlite3.Error, ValueError):
        fallback = Path(tempfile.mkdtemp(prefix="hacklab-")) / "tracking.sqlite"
        print(f"hacklab: tracking database unavailable, using {fallback}", file=sys.stderr)
        return Tracker(fallback)


def doctor(config: Config) -> dict:
    packages = {}
    for name in ("hacklab", "httpx", "pydantic", "pypdf", "openpyxl", "python-docx"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "missing"
    return {
        "python": platform.python_version(),
        "platform": platform.system(),
        "packages": packages,
        "tools": {
            tool: bool(shutil.which(tool))
            for tool in ("git", "uv", "docker", "podman", "nix", "bob")
        },
        "kvm_accessible": os.access("/dev/kvm", os.R_OK | os.W_OK),
        "mode": config.model.provider,
        "small_model_configured": bool(config.model.base_url and config.model.model),
        "small_model_key_present": bool(read_secret(config.model.api_key_env)),
        "jev_enabled": config.jev.enabled,
        "jev_key_present": bool(read_secret(config.jev.api_key_env)),
        "cache_mode": config.cache.mode,
        "embedding": {
            "enabled": config.embedding.enabled,
            "model_present": (Path(config.embedding.model_dir) / "tokenizer.json").is_file(),
        },
        "playbooks": config.agent.playbooks or None,
        "guards": {
            "injection": config.guard.injection,
            "compact_secrets": config.guard.compact_secrets,
            "allowed_actions": config.guard.allowed_actions,
        },
        "network_probed": False,
        "note": "Presence checks only; no authenticated API or VM boot verification.",
    }


def probe(config: Config, tracker: Tracker) -> dict:
    """One small tracked request to each configured model: is the contract right?"""
    from hacklab.providers import Budget, ProviderError, make_providers

    run_id = tracker.start_run("probe", config.model_dump(), mode=config.model.provider)
    report: dict = {}
    providers: list = []
    try:
        small, jev = make_providers(config, Budget(config.limits), tracker, run_id)
        providers = [small, jev]
        checks = [
            (
                "small_model",
                lambda: small.complete(
                    [{"role": "user", "content": 'Return ONLY this JSON object: {"ok": true}'}],
                    {"type": "object"},
                ),
            )
        ]
        if jev is not None:
            checks.append(
                (
                    "jev",
                    lambda: jev.judge(
                        {"text": "The invoice total is 120 EUR."},
                        {"amount": {"type": "noul", "instructions": "`text` states an amount."}},
                    ),
                )
            )
        for name, call in checks:
            try:
                report[name] = {"ok": True, "reply": call()}
            except ProviderError as exc:
                report[name] = {"ok": False, "error": str(exc)}
    except ProviderError as exc:
        report["setup"] = {"ok": False, "error": str(exc)}
    finally:
        for provider in providers:
            if provider is not None:
                provider.close()
        ok = bool(report) and all(item["ok"] for item in report.values())
        tracker.finish_run(run_id, "ok" if ok else "error")
    keys = (
        "provider",
        "model",
        "status",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "cost_usd",
        "metadata",
    )
    return {
        "ok": ok,
        **report,
        "calls": [{key: call.get(key) for key in keys} for call in tracker.calls(run_id)],
    }


def budget_report(config: Config, tracker: Tracker) -> dict:
    """Live spend in this tracking database against the configured team allowance."""
    spent = tracker.spend()
    limits = {
        "calls": config.budget.max_calls,
        "tokens": config.budget.max_tokens,
        "usd": config.budget.max_usd,
    }
    return {
        "spent": spent,
        "limits": limits,
        "remaining": {
            key: None if limit is None else max(0, limit - spent[key])
            for key, limit in limits.items()
        },
        "note": "Counts live requests recorded here; replays and other laptops are not included.",
    }


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(description="Small-model incident-response agent lab")
    app.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("HACKLAB_DB", ".state/tracking.sqlite")),
    )
    commands = app.add_subparsers(dest="command", required=True)
    for name in ("doctor", "probe", "solve", "benchmark", "submit", "budget", "cache"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", type=Path)
        if name in ("solve", "benchmark", "submit"):
            sub.add_argument("--root", type=Path, default=Path("bench/data"))
        if name == "solve":
            sub.add_argument("--task", type=Path, required=True)
        if name == "benchmark":
            sub.add_argument("--dataset", type=Path, default=Path("bench/smoke.jsonl"))
            sub.add_argument("--out", type=Path, default=Path("results/latest.json"))
            sub.add_argument("--repeat", type=int, default=1)
        if name == "submit":
            sub.add_argument("--input", type=Path, required=True)
            sub.add_argument("--output", type=Path, required=True)
            sub.add_argument("--workers", type=int, default=1)
            sub.add_argument(
                "--deadline-seconds",
                type=float,
                help="Tasks not started by then are answered with an abstention.",
            )
        if name == "cache":
            sub.add_argument("operation", choices=("stats", "clear"))
    runs = commands.add_parser("runs")
    runs.add_argument("--limit", type=int, default=20)
    stats = commands.add_parser("stats")
    stats.add_argument("--run-id")
    export = commands.add_parser("export")
    export.add_argument("--out", type=Path, required=True)
    comparison = commands.add_parser("compare")
    comparison.add_argument("left", type=Path)
    comparison.add_argument("right", type=Path)
    failed = commands.add_parser("failures")
    failed.add_argument("report", type=Path)
    practice = commands.add_parser("practice")
    practice.add_argument("--seed", type=int, required=True)
    practice.add_argument("--out", type=Path, required=True)
    practice.add_argument("--agents", type=int, default=40)
    practice.add_argument("--events", type=int, default=1500)
    decode = commands.add_parser("decode")
    decode.add_argument("file", type=Path, help="File holding the encoded text, or - for stdin")
    decode.add_argument("--key", action="append", default=[])
    indicators = commands.add_parser("iocs")
    indicators.add_argument("file", type=Path)
    embedding = commands.add_parser("embedding-fetch")
    embedding.add_argument(
        "--dir", type=Path, default=Path(".state/models/embeddinggemma-300m-onnx")
    )
    server = commands.add_parser("mock-server")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    return app


def _read_task(path: Path) -> Task:
    if path.stat().st_size > 100_000:
        raise ValueError("Task JSON exceeds 100 KB")
    return Task.model_validate_json(path.read_text())


def _private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _read_text(path: Path) -> str:
    if str(path) == "-":
        return sys.stdin.read(4_000_000)
    if path.stat().st_size > 64_000_000:
        raise ValueError("Input exceeds 64 MB")
    return path.read_text(errors="replace")


def _submit(args, config: Config, tracker: Tracker) -> dict:
    from hacklab.agent import run_task

    if args.input.resolve() == args.output.resolve():
        raise ValueError("Input and output must be different paths")
    if args.input.stat().st_size > 8_000_000:
        raise ValueError("Submission input exceeds 8 MB")
    if not 1 <= args.workers <= 16:
        raise ValueError("workers must be 1..16")
    # Validate the complete local contract before any API calls or output writes.
    tasks = [
        Task.model_validate_json(line)
        for line in args.input.read_text().splitlines()
        if line.strip()
    ]
    if not 1 <= len(tasks) <= 1000:
        raise ValueError("Input must contain 1..1000 tasks")
    deadline = (
        time.monotonic() + args.deadline_seconds if args.deadline_seconds is not None else None
    )

    def solve(task: Task) -> dict:
        if deadline is not None and time.monotonic() >= deadline:
            late = Answer(status="abstain", answer="", reason="run_deadline_reached")
            return {"id": task.id, "run_id": None, **late.model_dump()}
        run_id, answer = run_task(task, args.root, config, tracker)
        return {"id": task.id, "run_id": run_id, **answer.model_dump()}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream, ThreadPoolExecutor(args.workers) as pool:
        # Results arrive in input order and reach the disk one by one, so an
        # interrupted run keeps every answer it finished.
        for record in pool.map(solve, tasks):
            stream.write(json.dumps(record) + "\n")
            stream.flush()
    return {"output": str(args.output), "tasks": len(tasks)}


def main() -> int:
    args = parser().parse_args()
    load_dotenv(override=False)
    try:
        if args.command == "compare":
            from hacklab.benchmark import compare

            emit(compare(json.loads(args.left.read_text()), json.loads(args.right.read_text())))
            return 0
        if args.command == "failures":
            from hacklab.benchmark import failures

            emit(failures(json.loads(args.report.read_text())))
            return 0
        if args.command == "practice":
            from hacklab.practice import generate

            emit(generate(args.seed, args.out, args.agents, args.events))
            return 0
        if args.command == "decode":
            from hacklab.forensics.decode import candidate_keys, peel

            text = _read_text(args.file)
            decoded = peel(text.strip(), args.key + candidate_keys(text))
            emit(
                {
                    "layers": list(decoded.layers),
                    "text": decoded.text,
                    "binary_bytes": None if decoded.text is not None else len(decoded.data),
                }
            )
            return 0
        if args.command == "iocs":
            from hacklab.forensics.iocs import extract

            emit(extract(_read_text(args.file)))
            return 0
        if args.command == "embedding-fetch":
            from hacklab.embedding import REPOSITORY, fetch

            emit({"repository": REPOSITORY, "directory": str(args.dir), "sha256": fetch(args.dir)})
            return 0
        if args.command == "mock-server":
            from hacklab.mockserver import serve

            serve(args.host, args.port)
            return 0
        config = load_config(getattr(args, "config", None))
        if args.command == "doctor":
            emit(doctor(config))
            return 0
        if args.command == "cache":
            from hacklab.cache import ResponseCache

            cache = ResponseCache(Path(config.cache.path), "readwrite")
            emit({"removed": cache.clear()} if args.operation == "clear" else cache.stats())
            return 0
        tracker = open_tracker(args.db)
        if args.command == "probe":
            result = probe(config, tracker)
            emit(result)
            return int(not result["ok"])
        if args.command == "budget":
            emit(budget_report(config, tracker))
        elif args.command == "runs":
            emit(tracker.runs(args.limit))
        elif args.command == "stats":
            emit(tracker.summary(args.run_id))
        elif args.command == "export":
            tracker.export(args.out)
            emit({"export": str(args.out)})
        elif args.command == "benchmark":
            from hacklab.benchmark import benchmark

            report = benchmark(args.dataset, args.root, config, tracker, args.repeat)
            _private_json(args.out, report)
            emit({key: value for key, value in report.items() if key != "records"})
        elif args.command == "solve":
            from hacklab.agent import run_task

            run_id, answer = run_task(_read_task(args.task), args.root, config, tracker)
            emit({"run_id": run_id, **answer.model_dump()})
            return int(answer.status == "error")
        elif args.command == "submit":
            emit(_submit(args, config, tracker))
        return 0
    except Exception as exc:
        # Provider exceptions and validation errors may contain user inputs; never echo bodies.
        print(
            f"hacklab: {type(exc).__name__}; check configuration and local input contracts.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
