"""Local CLI. No network calls are made unless a live task is explicitly run."""

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

from hacklab.config import Config, load_config, read_secret
from hacklab.models import Task
from hacklab.tracking import Tracker


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


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
            for tool in (
                "nix",
                "uv",
                "bob",
                "git",
                "ichnora",
                "ast-index",
                "agent-evidence",
                "agent-tool-meter",
                "tabula",
            )
        },
        "kvm_accessible": os.access("/dev/kvm", os.R_OK | os.W_OK),
        "mode": config.model.provider,
        "small_model_configured": bool(config.model.base_url and config.model.model),
        "small_model_key_present": bool(read_secret(config.model.api_key_env)),
        "jev_enabled": config.jev.enabled,
        "jev_key_present": bool(read_secret(config.jev.api_key_env)),
        "network_probed": False,
        "note": "Presence checks only; no authenticated API or VM boot verification.",
    }


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(description="Small-model local agent lab")
    app.add_argument("--db", type=Path, default=Path(".state/tracking.sqlite"))
    commands = app.add_subparsers(dest="command", required=True)
    for name in ("doctor", "solve", "benchmark", "submit"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", type=Path)
        if name != "doctor":
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
    runs = commands.add_parser("runs")
    runs.add_argument("--limit", type=int, default=20)
    stats = commands.add_parser("stats")
    stats.add_argument("--run-id")
    export = commands.add_parser("export")
    export.add_argument("--out", type=Path, required=True)
    comparison = commands.add_parser("compare")
    comparison.add_argument("left", type=Path)
    comparison.add_argument("right", type=Path)
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


def main() -> int:
    args = parser().parse_args()
    load_dotenv(override=False)
    try:
        if args.command == "compare":
            from hacklab.benchmark import compare

            emit(compare(json.loads(args.left.read_text()), json.loads(args.right.read_text())))
            return 0
        config = load_config(getattr(args, "config", None))
        if args.command == "doctor":
            emit(doctor(config))
            return 0
        tracker = Tracker(args.db)
        if args.command == "runs":
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
            from hacklab.agent import run_task

            if args.input.resolve() == args.output.resolve():
                raise ValueError("Input and output must be different paths")
            if args.input.stat().st_size > 8_000_000:
                raise ValueError("Submission input exceeds 8 MB")
            # Validate the complete local contract before any API calls or output writes.
            tasks = [
                Task.model_validate_json(line)
                for line in args.input.read_text().splitlines()
                if line.strip()
            ]
            if not 1 <= len(tasks) <= 1000:
                raise ValueError("Input must contain 1..1000 tasks")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                for task in tasks:
                    run_id, answer = run_task(task, args.root, config, tracker)
                    stream.write(
                        json.dumps({"id": task.id, "run_id": run_id, **answer.model_dump()}) + "\n"
                    )
                    stream.flush()
            emit({"output": str(args.output), "tasks": len(tasks)})
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
