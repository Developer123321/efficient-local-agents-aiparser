"""Template: run the harness from inside the organizers' starter kit.

Copy this file next to the kit's own agent on the day and fill in the three
places marked TODO. It shows the whole seam: their tools become ToolSpecs,
their task becomes a Task, the Answer goes back in their shape.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from hacklab.agent import run_task
from hacklab.cli import open_tracker
from hacklab.config import load_config
from hacklab.models import Task
from hacklab.toolkit import ToolSpec

# TODO: names of the kit's tools that change something (revoke, quarantine,
# delete, block). They pass the action guard before they run.
MUTATING: set[str] = set()


def solve(
    question: str,
    *,
    task_id: str,
    data_dir: Path,
    documents: Iterable[str] = (),
    kit_tools: Iterable[Callable[..., Any]] = (),
    config_path: Path = Path("configs/live.toml"),
    tracking: Path = Path(".state/tracking.sqlite"),
) -> dict[str, Any]:
    """Answer one task of the kit and return a plain dict.

    `documents` are file names below `data_dir` that the task may read; leave
    it empty when the kit hands out data only through its tools.
    """
    config = load_config(config_path)
    tracker = open_tracker(tracking)
    tools = [ToolSpec.from_function(tool, mutating=tool.__name__ in MUTATING) for tool in kit_tools]
    task = Task(id=task_id, question=question, documents=list(documents))
    _, answer = run_task(task, data_dir, config, tracker, tools)
    # TODO: map to the organizers' answer format. These are the parts they can
    # ask for: the answer text, where it came from, what to do about it.
    return {
        "status": answer.status,
        "answer": answer.answer,
        "sources": [
            {"document": c.document, "locator": c.locator, "quote": c.quote}
            for c in answer.citations
        ],
        "actions": [{"type": a.type, "target": a.target} for a in answer.actions],
    }


# TODO: the kit's entry point calls solve() once per task, for example:
#
#     for task in kit.load_tasks():
#         result = solve(task.prompt, task_id=task.id, data_dir=kit.DATA,
#                        kit_tools=[kit.get_agent_log, kit.quarantine_agent])
#         kit.submit(task.id, result)
