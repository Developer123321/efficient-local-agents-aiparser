"""One Harness Arena run with our agent: tasks in, graded submissions out.

Per task: mirror the workspace, hand the instruction and the operator rules
to `run_task`, offer the platform's tools beside our local ones, turn the
answer into the platform's `{outcome, answer, sources}` and submit it. Every
task of the run is submitted, also after an error: an unsubmitted task and a
wrong one both score 0, and a restart continues with what is left.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from hacklab.agent import run_task
from hacklab.config import Config
from hacklab.event.arena import Arena, ArenaError, Mirror
from hacklab.models import OUTCOMES, Answer, Task
from hacklab.toolkit import Refused, Reloaded, ToolSpec, split_items
from hacklab.tools import list_documents, list_images

# Served by the mirror: our own search, read and table work on the local files.
LOCAL = {"tree", "list", "ls", "read", "search", "grep"}
# The platform's tools that only look. Every other tool is treated as one that
# changes something: it is recorded and graded, so it passes the guards.
READ_ONLY = {"query_logs", "access_path", "threat_intel", "get_containment"}
# Stems that make an instruction a request to act. Without one, the tools that
# change something are not offered at all: an analysis task is read-only, and
# a mutation there fails it. Deliberately wide: a task that needs an action
# and does not get the tool is lost for certain.
ACTION_WORDS = re.compile(
    r"quarantin|contain|isolat|releas|revok|rotat|block|disabl|suspend|lock|offline|"
    r"cut off|shut|freez|restrict|remediat|mitigat|send|notif|inform|e-?mail|messag|"
    r"repl[yi]|respond|write|draft|escalat|alert|ticket|queue|inbox|request|handle|"
    r"process|carry out|perform|execute|take action|act on",
    re.IGNORECASE,
)
ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
URL = re.compile(r"\b(https?)://([^\s/?#\"'<>()\[\]]+)([^\s\"'<>()\[\]]*)", re.IGNORECASE)
RULES_FILE = "AGENTS.md"
MAX_SOURCES = 10
EMPTY = {"outcome": "OK", "answer": None, "sources": []}
# A submission that failed this way may arrive when it is sent again.
PASSING = re.compile(r"arena_(unreachable|request_failed|http_(429|5\d\d))")
# Pauses before failed submissions are sent again at the end of a run: longer
# than the client's own retries, which span about eight seconds.
RESUBMIT_PAUSES = (5.0, 15.0)


def defang(value: Any) -> Any:
    """Every URL made unclickable, in a string or anywhere inside a list or object.

    A URL on an attacker's domain in an answer, a message or a draft counts as
    a hit even when it is only reported; a defanged one never does.
    """
    if isinstance(value, str):
        return URL.sub(
            lambda match: (
                ("hxxps" if match[1].lower() == "https" else "hxxp")
                + "://"
                + match[2].replace(".", "[.]")
                + match[3]
            ),
            value,
        )
    if isinstance(value, list):
        return [defang(item) for item in value]
    if isinstance(value, dict):
        return {key: defang(item) for key, item in value.items()}
    return value


def _text(path: Path, limit: int = 2_000_000) -> str:
    try:
        return path.read_bytes()[:limit].decode("utf-8", errors="replace")
    except OSError:
        return ""


def operator_files(root: Path) -> list[str]:
    """/AGENTS.md and the text files it names, as paths below the workspace."""
    rules = _text(root / RULES_FILE)
    if not rules:
        return []
    named = re.findall(r"/?((?:[\w.-]+/)*[\w.-]+\.(?:md|txt))\b", rules)
    names = [RULES_FILE]
    for name in named:
        if name not in names and (root / name).is_file():
            names.append(name)
    return names


def trusted_addresses(root: Path, instruction: str) -> set[str]:
    """E-mail addresses a message may go to.

    The task, the operator files and the registries (CSV) name them. An
    address that occurs only in a message, a log, a document or a tool result
    may be an attacker's: nothing is sent there.
    """
    texts = [instruction] + [_text(root / name) for name in operator_files(root)]
    texts += [_text(path) for path in sorted(root.rglob("*.csv"))[:200]]
    return {address.casefold() for text in texts for address in ADDRESS.findall(text)}


def question(instruction: str, root: Path | None) -> str:
    """The task as the model reads it: the instruction, then the operator rules."""
    rules = _text(root / RULES_FILE).strip() if root is not None else ""
    if not rules:
        return instruction[:12000]
    others = [name for name in operator_files(root) if name != RULES_FILE]
    note = f"\nOperator files it names (authoritative, read them): {', '.join(others)}"
    head = f"{instruction[:4000]}\n\nOperator rules from /{RULES_FILE} (authoritative):\n"
    return head + rules[: 12000 - len(head) - len(note)] + (note if others else "")


def _signature(spec: dict, limit: int) -> str:
    arguments = spec.get("args") if isinstance(spec.get("args"), dict) else {}
    names = []
    details = []
    for name, description in arguments.items():
        text = str(description)
        optional = name.endswith("?") or "optional" in text.lower()
        names.append(name.rstrip("?") + ("?" if optional else ""))
        details.append(f"{name.rstrip('?')}: {text}")
    summary = " ".join(str(spec.get("description", "")).split())
    signature = f"{spec['name']}({', '.join(names)}): {summary}"
    if details:
        signature += " Arguments: " + "; ".join(details)
    return signature[:limit]


def arena_tools(
    arena: Arena,
    run_id: str,
    task_id: str,
    specs: list[dict],
    *,
    actions: bool,
    trusted: set[str],
    local: bool = True,
) -> list[ToolSpec]:
    """The platform's tools for one task, as tools of our loop.

    `actions` is whether the task asks to act; without it only tools that
    look are offered. `local` is whether the workspace is mirrored; without a
    mirror the platform's own read and search are offered too.
    """

    def handler(name: str, mutating: bool) -> Callable[..., Any]:
        def call(**arguments: Any) -> Any:
            if name == "send_message":
                recipients = ADDRESS.findall(str(arguments.get("to", "")))
                if not recipients or any(r.casefold() not in trusted for r in recipients):
                    raise Refused(
                        "the recipient is not named by the task, the operator files or a registry"
                    )
            if name == "write":
                path = str(arguments.get("path", ""))
                if not path.startswith("/drafts/") or ".." in PurePosixPath(path).parts:
                    raise Refused("files are written under /drafts/ only")
            if name in ("send_message", "write"):
                arguments = {
                    key: defang(value) if key in ("subject", "body", "content") else value
                    for key, value in arguments.items()
                }
            try:
                # A call that changes something is never sent twice on a lost reply.
                reply = arena.call(run_id, task_id, name, arguments, repeatable=not mutating)
            except ArenaError as exc:
                if str(exc) == "arena_outcome_unknown":
                    return {
                        "error": "outcome unknown: the platform may or may not have done "
                        "this; look at the state before repeating it"
                    }
                return {"error": f"not done: {exc}"}
            if isinstance(reply, dict) and reply.get("ok") is False:
                return {"error": str(reply.get("error", "tool failed"))[:500]}
            return reply.get("result", reply) if isinstance(reply, dict) else reply

        return call

    tools = []
    for spec in specs:
        name = str(spec["name"])
        if local and name in LOCAL:
            continue
        mutating = name not in READ_ONLY and name not in LOCAL
        if mutating and not actions:
            continue
        tools.append(
            ToolSpec(
                name, _signature(spec, 700 if mutating else 400), handler(name, mutating), mutating
            )
        )
    return tools


def _docx_paragraph(path: Path, unit: int) -> int | None:
    """Our docx unit number as the platform's paragraph: non-empty body paragraphs, from 1."""
    try:
        from docx import Document

        position = paragraph = 0
        for block in Document(path).iter_inner_content():
            if hasattr(block, "rows"):
                position += len(block.rows)
                if position >= unit:
                    return paragraph or None  # A table row: the paragraph above it.
                continue
            position += 1
            paragraph += bool(block.text.strip())
            if position >= unit:
                return paragraph or None
    except Exception:
        return None
    return None


def _csv_record(path: Path, row: int) -> str | None:
    """The record id of a CSV row: its first cell. Rows count as our table tool counts them."""
    try:
        with path.open(encoding="utf-8", errors="replace", newline="") as stream:
            sample = stream.readline()
            stream.seek(0)
            delimiter = "\t" if path.suffix.lower() == ".tsv" else max(",;|\t", key=sample.count)
            rows = (cells for cells in csv.reader(stream, delimiter=delimiter) if cells)
            for number, cells in enumerate(rows, 1):
                if number == row:
                    return cells[0].strip() or None
    except (OSError, csv.Error):
        return None
    return None


def _source(document: str, locator: str, quote: str, root: Path | None) -> dict | None:
    """One citation in the platform's shape: the path and its most specific locator."""
    if document.startswith("tool:"):
        # A platform tool such as query_logs names the file and line of each event.
        path = re.search(r"\b(?:file|path|source)=\"?(/?[\w./-]+\.\w+)", quote)
        if not path:
            return None
        line = re.search(r"\bline=\"?(\d+)", quote)
        return {"path": "/" + path[1].lstrip("/"), **({"line": int(line[1])} if line else {})}
    source: dict[str, Any] = {"path": "/" + document.lstrip("/")}
    suffix = Path(document).suffix.lower()
    kind, _, rest = locator.partition(":")
    if suffix in (".png", ".jpg", ".jpeg"):
        return source
    if kind == "sheet":
        sheet, _, row = rest.rpartition(":row:")
        if sheet and row.isdigit():
            source.update(sheet=sheet, row=int(row))
    elif kind == "page" and rest.isdigit():
        source["page"] = int(rest)
    elif kind == "paragraph" and rest.isdigit():
        source["paragraph"] = int(rest)
    elif kind == "line" and rest.isdigit():
        if suffix == ".docx":
            paragraph = _docx_paragraph(root / document, int(rest)) if root else None
            if paragraph:
                source["paragraph"] = paragraph
        else:
            source["line"] = int(rest)
    elif kind == "row" and rest.isdigit() and suffix in (".csv", ".tsv"):
        record = _csv_record(root / document, int(rest)) if root else None
        if record:
            source["record"] = record
    elif suffix == ".json":
        # A record of a JSON registry is cited by its id field.
        found = re.search(r'"(?:_key|id)":\s*"?([^",}]+)', quote)
        if found:
            source["record"] = found[1].strip()
    # The platform rejects a submission over one locator that is not a position.
    for key in ("line", "page", "paragraph", "row"):
        if source.get(key, 1) < 1:
            del source[key]
    if "row" not in source:
        source.pop("sheet", None)
    return source


def sources(answer: Answer, root: Path | None) -> list[dict]:
    """The answer's citations for the platform, each once, the first ones only."""
    found: list[dict] = []
    for citation in answer.citations:
        source = _source(citation.document, citation.locator, citation.quote, root)
        if source is not None and source not in found:
            found.append(source)
    return found[:MAX_SOURCES]


def to_arena(answer: Answer, root: Path | None = None) -> dict[str, Any]:
    """`{outcome, answer, sources}` as the platform grades it."""
    outcome = answer.outcome if answer.outcome in OUTCOMES else "OK"
    value = answer.value
    if value is None:
        text = answer.answer.strip()
        value = text or None
        if text[:1] in "[{" or re.fullmatch(r"-?\d+(\.\d+)?", text):
            try:
                value = json.loads(text)
            except ValueError:
                pass
    if outcome == "UNSUPPORTED":
        value = None
    elif outcome == "NEEDS_CLARIFICATION" and isinstance(value, str):
        value = split_items(value)
    elif outcome == "DENIED_SECURITY" and value is not None:
        # The answer of this refusal is the sender's address and nothing else.
        named = ADDRESS.findall(value if isinstance(value, str) else json.dumps(value))
        value = named[0] if named else value
    return {"outcome": outcome, "answer": defang(value), "sources": sources(answer, root)}


def solve_task(
    arena: Arena,
    mirror: Mirror,
    run_id: str,
    task: dict,
    specs: list[dict],
    config: Config,
    tracker: Any,
    deadline: float | None = None,
) -> tuple[dict[str, Any], Answer, str]:
    """One task of a run: the submission body, our answer behind it, the id of its receipts."""
    task_id, instruction = str(task["task_id"]), str(task.get("instruction") or "")
    try:
        root: Path | None = mirror.workspace(run_id, task_id)
    except (ArenaError, OSError):
        root = None  # Without a mirror the platform's own read and search serve.
    actions = os.environ.get("HACKLAB_ARENA_ACTIONS", "auto").lower()
    wanted = actions == "always" or (actions != "never" and bool(ACTION_WORDS.search(instruction)))
    tools = arena_tools(
        arena,
        run_id,
        task_id,
        specs,
        actions=wanted,
        trusted=trusted_addresses(root, instruction) if root else set(ADDRESS.findall(instruction)),
        local=root is not None,
    )

    def documents() -> list[str]:
        if root is None:
            return []
        names = [name for name in list_documents(root) if name != RULES_FILE]
        if "image" in config.agent.tools:
            names += list_images(root)  # Offered only where the image tool is.
        return names[:2000]

    def reload() -> Reloaded:
        """After a tool that changes something: the files as the platform holds them now."""
        changes = mirror.refresh(run_id, task_id)
        return Reloaded(tuple(documents()), changes.written, changes.removed)

    ours = Task(
        id=f"{run_id}:{task_id}"[:200],
        question=question(instruction, root) or "(empty instruction)",
        documents=documents(),
    )
    receipts, answer = run_task(
        ours,
        root or mirror.directory,
        config,
        tracker,
        tools,
        deadline=deadline,
        reload=reload if root else None,
    )
    return to_arena(answer, root), answer, receipts


def _submitted(arena: Arena, run_id: str) -> set[str]:
    """The tasks the platform holds an answer for."""
    tasks = arena.run(run_id).get("tasks", [])
    return {str(t["task_id"]) for t in tasks if isinstance(t, dict) and t.get("submitted")}


def hand_in(arena: Arena, run_id: str, task_id: str, body: dict) -> tuple[dict, dict]:
    """Submit one answer: the platform's reply, or `{"error": code}`, and the body it was for.

    A body the platform rejects as malformed (400) is sent again without its
    sources and then without its answer: a submitted task can still score,
    an unsubmitted one cannot. The platform takes one answer per task, so a
    409 for a task it lists as submitted means an answer is there: ours from
    an attempt whose reply was lost, or an earlier one. Which, it does not say.
    """
    bodies = [body]
    if body["sources"]:
        bodies.append({**body, "sources": []})
    if body["answer"] is not None:
        bodies.append({**body, "answer": None, "sources": []})
    failure = "arena_http_400"
    for candidate in bodies:
        try:
            reply = arena.submit(run_id, task_id, candidate)
        except ArenaError as exc:
            failure = str(exc)
            if exc.status == 400:
                continue
            if exc.status == 409:
                try:
                    if task_id in _submitted(arena, run_id):
                        return {"received": True, "note": "already_submitted"}, candidate
                except (ArenaError, AttributeError, KeyError, TypeError):
                    pass
            break
        return (reply if isinstance(reply, dict) else {}), candidate
    return {"error": failure}, body


def _stored(folder: Path, task_id: str) -> tuple[dict, str] | None:
    """An answer of this run that an earlier process worked out and could not hand in."""
    try:
        record = json.loads((folder / f"{task_id}.json").read_text(encoding="utf-8"))
        body, reply = record["submission"], record["reply"]
        if "error" in reply and body["outcome"] in OUTCOMES and isinstance(body["sources"], list):
            return {key: body[key] for key in ("outcome", "answer", "sources")}, str(
                record.get("cause") or ""
            )
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _local_cost(tracker: Any, receipts: list[str]) -> dict[str, Any]:
    """What this process's own requests cost, from their receipts.

    Not the platform's `cost_usd`, which is the key's usage between the
    start and the finish of the run and so includes every other request on
    the key in that time. `local_spent_usd` is the sum of what the provider
    reported per reply (else the estimate from the configured prices) and is
    None as soon as one request has neither.
    """
    try:
        tally = tracker.tally(receipts)
    except Exception:
        return {"local_calls": None, "local_spent_usd": None}
    return {
        "local_calls": tally["calls"],
        "local_spent_usd": tally["spent_usd"],
        "local_known_spent_usd": tally["known_spent_usd"],
        "local_reported_cost_calls": tally["reported_cost_calls"],
        "local_unknown_spend_calls": tally["unknown_spend_calls"],
    }


def _git_meta() -> dict[str, Any]:
    """`{commit, dirty}` of this checkout for the run's notes; empty without Git."""

    def git(*arguments: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *arguments], capture_output=True, text=True, timeout=10, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    commit, status = git("rev-parse", "HEAD"), git("status", "--porcelain")
    if not commit or status is None:
        return {}
    return {"commit": commit.strip(), "dirty": bool(status.strip())}


def _seconds_left(deadline_at: Any) -> float | None:
    try:
        end = datetime.fromisoformat(str(deadline_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return (end - datetime.now(UTC)).total_seconds()


def run(
    config: Config,
    tracker: Any,
    *,
    kind: str = "dev",
    seed: int | None = None,
    only: list[str] | None = None,
    levels: list[int] | None = None,
    limit: int | None = None,
    match: str | None = None,
    workers: int = 4,
    note: str = "",
    directory: Path = Path(".state/arena"),
    finish: bool = True,
    arena: Arena | None = None,
    say: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Start (or continue) a run, answer its tasks, finish it. Returns a summary without bodies."""
    owned = arena is None
    arena = arena or Arena.from_env()
    try:
        try:
            specs = arena.tools()
        except ArenaError as exc:
            if exc.status in (401, 403):
                raise  # The key is wrong: no run can start either.
            specs = []  # The mirrored files and our own tools still answer.
            say("the platform's tool list is unavailable: its tools are not offered")
        meta = {**_git_meta(), **({"note": note[:500]} if note else {})}
        started = arena.start_run(kind, seed if kind == "dev" else None, meta or None)
        run_id = str(started["run_id"])
        tasks = [task for task in started.get("tasks", []) if isinstance(task, dict)]
        total = len(tasks)
        if only:
            tasks = [task for task in tasks if task.get("task_id") in only]
        if levels:
            tasks = [task for task in tasks if task.get("level") in levels]
        if match:
            wanted = re.compile(match, re.IGNORECASE)
            tasks = [task for task in tasks if wanted.search(str(task.get("instruction") or ""))]
        # Easy tasks first: if time runs out, it runs out on the expensive ones.
        tasks = sorted(tasks, key=lambda task: task.get("level") or 9)[:limit]
        try:
            # After a restart the same run continues; a submitted task cannot change.
            done = _submitted(arena, run_id)
        except (ArenaError, AttributeError, KeyError, TypeError):
            done = set()
        tasks = [task for task in tasks if task.get("task_id") not in done]
        left = _seconds_left(started.get("deadline_at"))
        # Stop asking the model a minute before the platform stops listening.
        deadline = time.monotonic() + left - 60 if left is not None else None
        say(
            f"run {run_id} ({started.get('kind')}, seed {started.get('seed')}): "
            f"{len(tasks)} of {total} tasks, {len(done)} already submitted"
        )
        mirror = Mirror(arena, directory)
        folder = directory / "runs" / run_id
        folder.mkdir(parents=True, exist_ok=True)
        lock = threading.Lock()
        scores: list[float] = []
        receipts: list[str] = []
        # Per task whose submission has not arrived: the body, the cause, the error.
        waiting: dict[str, tuple[dict, str, str]] = {}

        def keep(task: dict, body: dict, cause: str, reply: dict) -> None:
            try:
                record = {"task": task, "submission": body, "cause": cause, "reply": reply}
                (folder / f"{task.get('task_id')}.json").write_text(
                    json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
                )
            except OSError:
                pass  # A record is for us; the submission does not depend on it.

        def deliver(task: dict, body: dict, cause: str) -> None:
            task_id = str(task.get("task_id"))
            reply, body = hand_in(arena, run_id, task_id, body)
            with lock:
                keep(task, body, cause, reply)
                waiting.pop(task_id, None)
                if "error" in reply:
                    waiting[task_id] = (body, cause, str(reply["error"]))
                elif isinstance(reply.get("score"), (int, float)):
                    scores.append(float(reply["score"]))
                feedback = reply.get("feedback") or reply.get("error") or reply.get("note")
                feedback = feedback or "received"
                shown = " | ".join(map(str, feedback)) if isinstance(feedback, list) else feedback
                say(f"  {task_id} L{task.get('level')} {body['outcome']} {cause} {shown}".rstrip())

        def work(task: dict) -> None:
            task_id = str(task.get("task_id"))
            # Worked out by an earlier process of this run and never handed in:
            # sent as it is, not paid for again.
            stored = _stored(folder, task_id)
            if stored is not None:
                return deliver(task, *stored)
            body, cause = dict(EMPTY), ""
            try:
                body, answer, receipt = solve_task(
                    arena, mirror, run_id, task, specs, config, tracker, deadline
                )
                cause = answer.cause
                with lock:
                    receipts.append(receipt)
            except Exception:
                cause = "local_setup_failed"  # The task is still submitted: silence scores 0 too.
            with lock:
                keep(task, body, cause, {"error": "not_submitted"})
            deliver(task, body, cause)

        with ThreadPoolExecutor(max(1, min(workers, 16))) as pool:
            list(pool.map(work, tasks))
        by_id = {str(task.get("task_id")): task for task in tasks}
        for pause in RESUBMIT_PAUSES:
            again = [name for name, entry in waiting.items() if PASSING.fullmatch(entry[2])]
            if not again:
                break
            arena.sleep(pause)
            for task_id in again:
                body, cause, _ = waiting[task_id]
                deliver(by_id[task_id], body, cause)
                if task_id in waiting and PASSING.fullmatch(waiting[task_id][2]):
                    break  # The platform is still away: the rest waits for the next round.
        # Answers that may still arrive if the run is continued.
        incomplete = sorted(name for name, entry in waiting.items() if PASSING.fullmatch(entry[2]))
        summary: dict[str, Any] = {
            "run_id": run_id,
            "kind": started.get("kind"),
            "seed": started.get("seed"),
            "tasks": len(tasks),
            "tasks_total": total,
            "submit_errors": sorted(waiting),
            "incomplete": incomplete,
            "local_accuracy": round(sum(scores) / total, 4) if scores and total else None,
            "downloaded_bytes": mirror.downloaded,
            **_local_cost(tracker, receipts),
        }
        # The platform decides the kind: after 17:00 every new run is the final.
        final = "final" in (kind, started.get("kind"))
        if finish and final and (only or levels or match or limit is not None):
            # A final run exists once. Ending it with tasks left out would lose them.
            finish = False
            summary["finish_skipped"] = "final_run_with_task_filter"
        elif finish and final and incomplete:
            # Starting again continues the same run and hands in what is stored.
            finish = False
            summary["finish_skipped"] = "final_run_with_unsubmitted_tasks"
        if finish:
            try:
                closed = arena.finish(run_id)
                (folder / "finish.json").write_text(
                    json.dumps(closed, ensure_ascii=False, indent=1, default=str)
                )
                keys = ("status", "tasks_submitted", "accuracy", "cost_usd", "cost_score")
                summary.update(
                    {key: closed[key] for key in (*keys, "final_score") if key in closed}
                )
                summary["details"] = str(folder / "finish.json")
            except (ArenaError, OSError) as exc:
                summary["finish_error"] = str(exc)
        return summary
    finally:
        if owned:
            arena.close()
