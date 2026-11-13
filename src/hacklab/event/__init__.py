"""Everything that is specific to the event's scenario, in one place inside the package.

- `playbooks.py`: code-only solvers for the task types revealed at the opening.
- `skills/`: short recipes added to the prompt of matching tasks.
- `adapter.py`: the seam to the organizers' starter kit.

`configs/live.toml` points at the first two. They are part of the package, so
they are in the wheel and in the submission image without another COPY line,
and they are found wherever the working directory is.
"""
