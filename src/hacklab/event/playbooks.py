"""Code-only solvers for the task types revealed at the opening.

Empty until then, so every task goes to the model. Add one solver per task
type, the most frequent type first; `configs/live.toml` already points here.
`examples/event_playbooks.py` holds a worked example and `hacklab.practice`
nine solvers for the rehearsal incident.

Rules that keep a solver from failing on the unseen final scenario:

- Take names of files, columns and values from the data or the task text,
  not from memory of the development set. `workspace.documents.profile()`
  tells which column holds a value.
- Return None whenever the data does not look as expected: the model takes over.
- Cite the rows the answer rests on; right sources are part of the score.

    @playbooks.register(r"<words that identify the task type>")
    def solver(task: Task, workspace: Workspace) -> Answer | None:
        rows = workspace.rows("<log>", {"<column>": "<value>"})
        later = workspace.after("<log>", key="<actor column>",
                                first={"<column>": "<trigger>"}, where={...})
        found = workspace.call("<kit tool>", agent_id="...")   # the organizers' tool
        return workspace.answer(text, [workspace.cite("<log>", row) for row in rows])
"""

from hacklab.playbooks import Playbooks

playbooks = Playbooks()
