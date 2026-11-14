"""Everything that is specific to the event's scenario, in one place inside the package.

- `arena.py`: the Harness Arena Task API and the local mirror of a task's files.
- `arena_run.py`: one run: tasks in, our agent, `{outcome, answer, sources}` out.
- `arena-rules.md`: the event's rules, appended to the system prompt (`[agent] rules`).
- `playbooks.py`: code-only solvers for task types that code can answer.
- `skills/`: short recipes added to the prompt of matching tasks.

`configs/live.toml` points at them. They are part of the package, so they are
in the wheel and in the submission image without another COPY line, and they
are found wherever the working directory is.
"""
