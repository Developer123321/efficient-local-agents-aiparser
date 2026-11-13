"""Template: code-only solvers for the task types revealed at the opening.

Copy this file into the package you submit, add one solver per task type, and
point the configuration at it:

    [agent]
    playbooks = "yourpackage.event_playbooks:playbooks"

A solver gets the task and a workspace. It returns an Answer built with
`workspace.answer(...)`, or None to hand the task to the model. Rules that
keep a solver from failing on the unseen final scenario:

- Take names of files, columns and values from the data or the task text,
  not from memory of the development set. `workspace.documents.profile()`
  tells which column holds a value.
- Return None whenever the data does not look as expected.
- Cite the rows the answer rests on; right sources are part of the score.
"""

from __future__ import annotations

import re

from hacklab.models import Answer, Task
from hacklab.playbooks import Playbooks, Workspace

playbooks = Playbooks()


def column_of(workspace: Workspace, document: str, value: str) -> str | None:
    """The one column of a table in which `value` occurs as a whole cell."""
    found = [
        column
        for column, facts in workspace.documents.profile(document).items()
        if any(known.casefold() == value.casefold() for known in facts.get("values", {}))
    ]
    return found[0] if len(found) == 1 else None


@playbooks.register(
    r"how many (?:\w+ )*?events? (?:with|of type|named) [`'\"]?(?P<value>[\w.:/-]+)"
)
def count_events(task: Task, workspace: Workspace) -> Answer | None:
    """Example: "How many events with exec_payload are in the audit log?"

    Works for any table of the task and any column value named in the
    question; it does not know the rehearsal's file or column names.
    """
    value = re.search(r"(?:with|of type|named) [`'\"]?([\w.:/-]+)", task.question).group(1)
    for entry in workspace.documents.inventory():
        if not entry.get("columns"):
            continue
        column = column_of(workspace, entry["document"], value)
        if column is None:
            continue
        rows = workspace.documents.rows(entry["document"], {column: value})
        evidence = [workspace.cite(entry["document"], unit) for unit in rows[:30]]
        return workspace.answer(str(len(rows)), evidence)
    return None


# TODO on the day, one per task type, most frequent type first:
#
# @playbooks.register(r"<words that identify the task type>")
# def solver(task: Task, workspace: Workspace) -> Answer | None:
#     rows = workspace.documents.rows("<log>", {"<column>": "<value>"})
#     ...
#     return workspace.answer(text, evidence_ids, actions)
