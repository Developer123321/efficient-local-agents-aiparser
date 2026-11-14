"""Local CLI. No network calls are made unless a live task is explicitly run."""

import argparse
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import ValidationError

from hacklab.config import Config, load_config, read_secret
from hacklab.models import Answer, Task
from hacklab.tracking import NoReceipts, Tracker


def emit(data):
    try:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    except UnicodeEncodeError:
        # A console that cannot show every character, as on Windows code pages.
        print(json.dumps(data, ensure_ascii=True, indent=2, default=str))


def open_tracker(path: Path) -> Tracker | NoReceipts:
    """Receipts must never stop a run: fall back to a temporary database, then to none."""
    try:
        return Tracker(path)
    except (OSError, sqlite3.Error, ValueError):
        pass
    try:
        fallback = Path(tempfile.mkdtemp(prefix="hacklab-")) / "tracking.sqlite"
        tracker = Tracker(fallback)
    except (OSError, sqlite3.Error, ValueError):
        print("hacklab: no writable place for receipts, running without them", file=sys.stderr)
        return NoReceipts()
    print(f"hacklab: tracking database unavailable, using {fallback}", file=sys.stderr)
    return tracker


def _engine(name: str) -> str:
    """Whether a container engine is installed and answers: its local daemon, no network."""
    if not shutil.which(name):
        return "missing"
    try:
        done = subprocess.run([name, "info"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return "installed, not answering"
    # Docker installed but not usable is the common case: the daemon is not
    # running, or the user is not in the docker group.
    return "ready" if done.returncode == 0 else "installed, not usable by this user"


def doctor(config: Config) -> dict:
    """What is installed and configured, and `problems` that would cost answers at run time."""
    from hacklab import playbooks as registry
    from hacklab.agent import skill_directory, skill_files

    packages = {}
    for name in ("hacklab", "httpx", "pydantic", "pypdf", "openpyxl", "python-docx"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "missing"
    problems = []
    solvers: int | None = None
    if config.agent.playbooks:
        try:
            solvers = len(registry.load(config.agent.playbooks))
        except Exception:
            problems.append(f"playbooks {config.agent.playbooks!r} cannot be loaded")
    recipes: int | None = None
    if config.agent.skills:
        recipes = len(skill_files(config.agent.skills))
        if not recipes:
            where = skill_directory(config.agent.skills)
            problems.append(f"no skill file with a match: line in {str(where)!r}")
    if config.agent.rules and not skill_directory(config.agent.rules).is_file():
        problems.append(f"rules file {config.agent.rules!r} cannot be found")
    return {
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "packages": packages,
        "tools": {tool: bool(shutil.which(tool)) for tool in ("git", "uv", "just", "nix", "bob")},
        "container_engines": {name: _engine(name) for name in ("docker", "podman")},
        "kvm_accessible": os.access("/dev/kvm", os.R_OK | os.W_OK),
        "mode": config.model.provider,
        "small_model_configured": bool(config.model.base_url and config.model.model),
        "small_model_key_present": bool(read_secret(config.model.api_key_env)),
        "jev_enabled": config.jev.enabled,
        "jev_key_present": bool(read_secret(config.jev.api_key_env)),
        "embedding_enabled": config.embedding.enabled,
        "embedding_configured": bool(
            config.embedding.model and (config.embedding.base_url or config.model.base_url)
        ),
        "cache_mode": config.cache.mode,
        "playbooks": {"reference": config.agent.playbooks or None, "solvers": solvers},
        "skills": {"reference": config.agent.skills or None, "files": recipes},
        "rules": config.agent.rules or None,
        "arena_key_present": bool(read_secret("ARENA_API_KEY")),
        "guards": {
            "injection": config.guard.injection,
            "compact_secrets": config.guard.compact_secrets,
            "allowed_actions": config.guard.allowed_actions,
        },
        "problems": problems,
        "network_probed": False,
        "note": "Presence checks only; no authenticated API or VM boot verification.",
    }


def blank_png(side: int = 16) -> bytes:
    """A small white PNG made here: the probe's picture, with nothing in it to read."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    rows = (b"\x00" + b"\xff" * (3 * side)) * side
    header = struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def probe(config: Config, tracker: Tracker) -> dict:
    """One small tracked request to each configured model: is the contract right?"""
    from hacklab.providers import Budget, ProviderError, make_embedder, make_providers

    run_id = tracker.start_run("probe", config.model_dump(), mode=config.model.provider)
    report: dict = {}
    providers: list = []
    try:
        budget = Budget(config.limits)
        small, jev = make_providers(config, budget, tracker, run_id)
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
        if "image" in config.agent.tools:
            # Does the endpoint take a picture at all? One more small request.
            checks.append(
                (
                    "image",
                    lambda: {"read": small.describe(blank_png(), "image/png")[:200]},
                )
            )
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
        if config.embedding.enabled:
            # Built inside the check: a missing name or key is this model's
            # failure, not the probe's.
            def embedding() -> dict:
                embedder = make_embedder(config, budget, tracker, run_id)
                providers.append(embedder)
                passages = ["Agent a7 was quarantined at 02:14.", "Lunch is at 12:30."]
                scores = embedder.similarity("Which agent was quarantined?", passages)
                return {"similarity": [round(score, 3) for score in scores]}

            checks.append(("embedding", embedding))
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
        "reported_cost_usd",
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
        "note": (
            "Counts live requests recorded here, at the cost the provider reported or else "
            "the estimate from the configured prices; replays and other laptops are not included."
        ),
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
        if name == "doctor":
            sub.add_argument(
                "--strict", action="store_true", help="Exit with 1 if a problem is found."
            )
        if name in ("solve", "benchmark"):
            sub.add_argument("--root", type=Path, default=Path("bench/data"))
        if name == "solve":
            sub.add_argument("--task", type=Path, required=True)
        if name == "benchmark":
            sub.add_argument("--dataset", type=Path, default=Path("bench/smoke.jsonl"))
            sub.add_argument("--out", type=Path, default=Path("results/latest.json"))
            sub.add_argument("--repeat", type=int, default=1)
            sub.add_argument(
                "--workers",
                type=int,
                default=1,
                help="Cases run side by side: the same requests in less time.",
            )
        if name == "submit":
            sub.add_argument("--input", type=Path, required=True)
            sub.add_argument("--output", type=Path, required=True)
            sub.add_argument(
                "--root", type=Path, help="Document directory; default: where the input lies."
            )
            sub.add_argument("--workers", type=int, default=1)
            sub.add_argument(
                "--deadline-seconds",
                type=float,
                help="The end of the run: later, only playbooks answer and no request "
                "outlives it. Every task has a line in the output at any moment.",
            )
            sub.add_argument(
                "--all-documents",
                action="store_true",
                help="A task that lists no documents may read every document below --root.",
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
    arena = commands.add_parser("arena", help="The event's Task API (ARENA_API_KEY in .env)")
    actions = arena.add_subparsers(dest="arena_command", required=True)
    run = actions.add_parser("run", help="Start a run, answer its tasks, finish it")
    run.add_argument("--config", type=Path)
    run.add_argument(
        "--kind",
        choices=("dev", "final"),
        default=os.environ.get("ARENA_RUN_KIND") or "dev",
        help="Default: $ARENA_RUN_KIND, else dev. The final is started by the organizers.",
    )
    run.add_argument("--seed", type=int, help="Replay the tasks of an earlier dev run")
    run.add_argument("--tasks", help="Only these task ids, comma-separated, e.g. t01,t03")
    run.add_argument("--level", help="Only these levels, comma-separated, e.g. 1 or 1,2")
    run.add_argument("--match", help="Only tasks whose instruction matches this regex")
    run.add_argument("--limit", type=int, help="At most this many of the selected tasks")
    run.add_argument("--workers", type=int, default=4, help="Tasks worked on side by side")
    run.add_argument("--note", default="", help="One line about this run for the dashboard")
    run.add_argument(
        "--no-finish", action="store_true", help="Leave the run open: no score, no answers"
    )
    for name in ("me", "payloads", "submissions"):
        actions.add_parser(name)
    show = actions.add_parser("show", help="A run with grades; a finished dev run with answers")
    show.add_argument("run_id")
    server = commands.add_parser("mock-server")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    return app


def _profile(args: argparse.Namespace) -> Path | None:
    """`--config`, or for an arena run the candidate profile beside the working directory."""
    given = getattr(args, "config", None)
    if given is None and args.command == "arena" and Path("configs/live.toml").is_file():
        return Path("configs/live.toml")
    return given


def _arena_directory() -> Path:
    return Path(os.environ.get("HACKLAB_ARENA_DIR") or ".state/arena")


def _arena(args: argparse.Namespace) -> int:
    """Read-only views of the team's state on the platform."""
    from hacklab.event.arena import Arena

    arena = Arena.from_env()
    try:
        if args.arena_command == "show":
            found = arena.run(args.run_id)
            target = _arena_directory() / "runs" / Path(args.run_id).name / "run.json"
            _private_json(target, found)
            keys = ("run_id", "kind", "seed", "status", "accuracy", "cost_usd", "cost_score")
            summary = {key: found.get(key) for key in (*keys, "final_score")}
            emit({**summary, "tasks": len(found.get("tasks", [])), "details": str(target)})
        else:
            emit(getattr(arena, args.arena_command)())
    finally:
        arena.close()
    return 0


def _read_task(path: Path) -> Task:
    if path.stat().st_size > 100_000:
        raise ValueError("Task JSON exceeds 100 KB")
    return Task.model_validate_json(path.read_text(encoding="utf-8"))


def _private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _read_text(path: Path) -> str:
    if str(path) == "-":
        return sys.stdin.read(4_000_000)
    if path.stat().st_size > 64_000_000:
        raise ValueError("Input exceeds 64 MB")
    return path.read_text(encoding="utf-8", errors="replace")


_ID_KEYS = ("id", "task_id", "taskId", "uid", "name")
_QUESTION_KEYS = ("question", "prompt", "instruction", "query", "task", "text", "input")
_DOCUMENT_KEYS = ("documents", "files", "docs", "attachments", "paths")


def read_records(text: str) -> list[Any]:
    """The task records of an input file: JSON Lines, a JSON array, or an object holding one.

    A line that is not JSON becomes None, so that it still gets an answer line.
    """
    try:
        parsed = json.loads(text)
    except ValueError:
        records = []
        for line in text.splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except ValueError:
                    records.append(None)
        return records
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("tasks", "items", "questions", "data"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
    return [parsed]


def task_from(record: Any, position: int) -> tuple[Task | None, str, list[str]]:
    """A Task from one record of the organizers' input, under the usual key names.

    Returns the task (None if the record holds no question), its ID, and the
    keys that were not used. Unused keys never reach the model, so a reference
    answer left in a development file stays out of the prompt; they are
    reported, so that a key the agent needs is noticed and mapped.
    """
    if isinstance(record, dict) and isinstance(record.get("task"), dict):
        record = record["task"]  # A benchmark case: {task, expected, category}.
    if isinstance(record, str):
        record = {"question": record}
    if not isinstance(record, dict):
        return None, f"task-{position}", []
    used: set[str] = set()

    def first(keys: tuple[str, ...], kinds: tuple[type, ...]) -> Any:
        for key in keys:
            value = record.get(key)
            if isinstance(value, kinds) and not isinstance(value, bool) and value not in ("", []):
                used.add(key)
                return value
        return None

    identifier = first(_ID_KEYS, (str, int))
    question = first(_QUESTION_KEYS, (str,))
    documents = first(_DOCUMENT_KEYS, (list,)) or []
    constraints = first(("constraints",), (dict,)) or {}
    task_id = str(identifier).strip()[:200] if identifier is not None else ""
    task_id = task_id or f"task-{position}"
    unused = sorted(str(key) for key in record if key not in used)
    if question is None or not question.strip():
        return None, task_id, unused
    try:
        task = Task(
            id=task_id,
            question=question[:12000],
            documents=[name for name in documents if isinstance(name, str)][:2000],
            constraints=constraints,
        )
    except ValidationError:
        return None, task_id, unused
    return task, task_id, unused


def _write_lines(path: Path, lines: list[dict]) -> None:
    """Replace the output with these lines in one step; readable by whoever evaluates it."""
    text = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    part = path.with_name(path.name + ".part")
    try:
        with open(part, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(part, path)
    except OSError:
        # The output is a mounted file, or its directory is read-only: write in place.
        with open(path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)


def _submit(args, config: Config, tracker: Tracker | NoReceipts) -> dict:
    """Answer every task of the input file. Built for the one run that counts.

    A record that cannot be read gets an error line instead of ending the run,
    and the output holds one valid line per task from the first moment on: a
    run that is stopped early leaves a complete file, not a truncated one.
    """
    from hacklab.agent import run_task
    from hacklab.tools import list_documents

    if args.input.resolve() == args.output.resolve():
        raise ValueError("Input and output must be different paths")
    if args.input.stat().st_size > 64_000_000:
        raise ValueError("Submission input exceeds 64 MB")
    if not 1 <= args.workers <= 32:
        raise ValueError("workers must be 1..32")
    records = read_records(args.input.read_text(encoding="utf-8", errors="replace"))
    if not 1 <= len(records) <= 5000:
        raise ValueError("Input must contain 1..5000 tasks")
    root = args.root if args.root is not None else args.input.parent
    everything: list[str] = []
    if args.all_documents:
        # The task file and the answers are not evidence, also when they lie in --root.
        own = {args.input, args.output, args.output.with_name(args.output.name + ".part")}
        skipped = {path.resolve() for path in own}
        base = root.resolve()
        everything = [name for name in list_documents(root) if base / name not in skipped]
    started = time.monotonic()
    deadline = started + args.deadline_seconds if args.deadline_seconds is not None else None
    tasks = [task_from(record, position) for position, record in enumerate(records, 1)]

    def line(task_id: str, run_id: str | None, answer: Answer) -> dict:
        return {"id": task_id, "run_id": run_id, **answer.model_dump()}

    def stopped(cause: str, status: str = "abstain") -> Answer:
        return Answer(status=status, answer="", reason=cause, cause=cause)

    lines = [
        line(
            task_id, None, stopped("not_finished") if task else stopped("task_unreadable", "error")
        )
        for task, task_id, _ in tasks
    ]
    lock = threading.Lock()
    written = [0.0]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_lines(args.output, lines)

    def solve(position: int) -> None:
        task, task_id, _ = tasks[position]
        if task is None:
            return
        if not task.documents and everything:
            task = task.model_copy(update={"documents": everything})
        try:
            run_id, answer = run_task(task, root, config, tracker, deadline=deadline)
        except Exception:
            # run_task promises an Answer; this keeps the promise for the file.
            run_id, answer = None, stopped("local_setup_failed", "error")
        with lock:
            lines[position] = line(task_id, run_id, answer)
            # Rewriting a long file for every answer would cost more than it protects.
            if time.monotonic() - written[0] >= 0.5 or len(lines) <= 200:
                _write_lines(args.output, lines)
                written[0] = time.monotonic()

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(solve, range(len(tasks))))
    _write_lines(args.output, lines)
    statuses: dict[str, int] = {}
    for record in lines:
        statuses[record["status"]] = statuses.get(record["status"], 0) + 1
    return {
        "output": str(args.output),
        "tasks": len(lines),
        "statuses": statuses,
        "unreadable": sum(task is None for task, _, _ in tasks),
        # Keys of the input that no part of the agent has seen.
        "ignored_keys": sorted({key for _, _, unused in tasks for key in unused}),
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }


def main() -> int:
    args = parser().parse_args()
    load_dotenv(override=False)
    try:
        if args.command == "compare":
            from hacklab.benchmark import compare

            left = json.loads(args.left.read_text(encoding="utf-8"))
            emit(compare(left, json.loads(args.right.read_text(encoding="utf-8"))))
            return 0
        if args.command == "failures":
            from hacklab.benchmark import failures

            emit(failures(json.loads(args.report.read_text(encoding="utf-8"))))
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
        if args.command == "mock-server":
            from hacklab.mockserver import serve

            serve(args.host, args.port)
            return 0
        if args.command == "arena" and args.arena_command != "run":
            return _arena(args)
        config = load_config(_profile(args))
        if args.command == "doctor":
            report = doctor(config)
            emit(report)
            return int(bool(args.strict and report["problems"]))
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

            report = benchmark(args.dataset, args.root, config, tracker, args.repeat, args.workers)
            _private_json(args.out, report)
            emit({key: value for key, value in report.items() if key != "records"})
        elif args.command == "solve":
            from hacklab.agent import run_task

            run_id, answer = run_task(_read_task(args.task), args.root, config, tracker)
            emit({"run_id": run_id, **answer.model_dump()})
            return int(answer.status == "error")
        elif args.command == "submit":
            emit(_submit(args, config, tracker))
        elif args.command == "arena":
            from hacklab.event.arena_run import run

            if not 1 <= args.workers <= 16:
                raise ValueError("workers must be 1..16")
            # argparse checks what is typed, not a default from the environment.
            if args.kind not in ("dev", "final"):
                raise ValueError("ARENA_RUN_KIND must be dev or final")
            if args.match:
                re.compile(args.match)
            summary = run(
                config,
                tracker,
                kind=args.kind,
                seed=args.seed,
                only=args.tasks.split(",") if args.tasks else None,
                levels=[int(level) for level in args.level.split(",")] if args.level else None,
                limit=args.limit,
                match=args.match,
                workers=args.workers,
                note=args.note,
                directory=_arena_directory(),
                finish=not args.no_finish,
            )
            emit(summary)
            # Answers whose submission did not arrive: starting again continues the run.
            return int(bool(summary["incomplete"]))
        return 0
    except Exception as exc:
        # Provider exceptions and validation errors may contain user inputs; never echo bodies.
        from hacklab.event.arena import ArenaError

        if isinstance(exc, ArenaError):
            # Its message is one of our own fixed codes, never a reply body.
            hints = {
                "arena_key_missing": "ARENA_API_KEY is empty: enter the platform key in .env",
                "arena_http_401": "the platform refused ARENA_API_KEY",
                "arena_http_403": "the platform refused this request for this key",
                "arena_http_409": "the run is finished, past its deadline or already submitted",
                "arena_unreachable": "no connection to ARENA_BASE_URL",
            }
            hint = hints.get(str(exc), "see docs/hackathon.md, the platform")
            print(f"hacklab: {exc}; {hint}.", file=sys.stderr)
            return 2
        print(
            f"hacklab: {type(exc).__name__}; check configuration and local input contracts.",
            file=sys.stderr,
        )
        if os.environ.get("HACKLAB_DEBUG"):
            # Where it failed, without the message: file, line and function only.
            print("".join(traceback.format_tb(exc.__traceback__)), file=sys.stderr, end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
