"""The seam to the organizers' starter kit: their task in, their answer shape out.

Fill in the places marked TODO on the day. The kit's entry point calls
`solve` once per task; the rest of the harness stays as it is.

    from hacklab.event.adapter import solve

    for task in kit.load_tasks():
        result = solve(task.prompt, task_id=task.id, data_dir=kit.DATA,
                       kit_tools=[kit.get_agent_log, kit.quarantine_agent],
                       model=kit.chat)          # only if the kit brings its own client
        kit.submit(task.id, result)
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hacklab.agent import run_task
from hacklab.cli import open_tracker
from hacklab.config import load_config
from hacklab.models import Answer, Task
from hacklab.toolkit import ToolSpec
from hacklab.tools import list_documents

# TODO: names of the kit's tools that change something (revoke, quarantine,
# delete, block). They pass the action guard before they run.
MUTATING: set[str] = set()


def to_kit(answer: Answer) -> dict[str, Any]:
    """The answer in the organizers' format.

    TODO: map to what they ask for. These are the parts they can ask for: the
    answer text, where it came from, what to do about it. `cause` is set when
    the harness, not the model, ended the task.
    """
    return {
        "status": answer.status,
        "answer": answer.answer,
        "sources": [
            {"document": c.document, "locator": c.locator, "quote": c.quote}
            for c in answer.citations
        ],
        "actions": [{"type": a.type, "target": a.target} for a in answer.actions],
    }


def snapshot(directory: Path, name: str, rows: Iterable[Any]) -> str:
    """Write what a kit tool returned as a document, and return its name.

    If the kit hands out logs only through its tools, call those tools from
    code, snapshot the rows, and list the names in `documents`: then table,
    after, graph and decode work on them, and every row can be cited.
    """
    document = name if Path(name).suffix else f"{name}.jsonl"
    target = directory / document
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            record = row if isinstance(row, dict) else {"text": str(row)}
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return document


def solve(
    question: str,
    *,
    task_id: str,
    data_dir: Path,
    documents: Iterable[str] | None = (),
    kit_tools: Iterable[Callable[..., Any]] = (),
    model: Callable[[list[dict[str, str]]], Any] | None = None,
    deadline: float | None = None,
    config_path: Path = Path("configs/live.toml"),
    tracking: Path = Path(".state/tracking.sqlite"),
) -> dict[str, Any]:
    """Answer one task of the kit and return a plain dict; never raises for a task.

    `documents` are file names below `data_dir` that the task may read. Empty
    means none, for a kit that hands out data only through its tools; None
    means every document below `data_dir`. `model` is the kit's own chat
    function, if requests must go through it: it takes the messages and
    returns the reply text or a chat completion. `deadline` is the end of the
    run on `time.monotonic()`.
    """
    config = load_config(config_path)
    tracker = open_tracker(tracking)
    tools = [ToolSpec.from_function(tool, mutating=tool.__name__ in MUTATING) for tool in kit_tools]
    names = list_documents(data_dir) if documents is None else list(documents)
    try:
        task = Task(
            id=str(task_id)[:200] or "task", question=question[:12000], documents=names[:2000]
        )
    except ValidationError:
        unreadable = Answer(status="error", answer="", reason="task_unreadable")
        return to_kit(unreadable.model_copy(update={"cause": "task_unreadable"}))
    _, answer = run_task(task, data_dir, config, tracker, tools, deadline=deadline, model=model)
    return to_kit(answer)
