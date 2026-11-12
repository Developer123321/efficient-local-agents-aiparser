"""Deterministic solvers that answer a recognised task without a model call.

A model turn costs tokens and can be wrong; code that computes the answer from
the logs costs nothing and is repeatable. Once the scenario's task types are
known, write one solver per type and register it by a pattern of the task
text. A solver returns None, or fails, to hand the task on to the model.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from hacklab.evidence import Ledger
from hacklab.models import Action, Answer, Task
from hacklab.toolkit import Toolbox
from hacklab.tools import ToolRegistry, Unit


@dataclass
class Workspace:
    """What a solver works with: the task's documents and the evidence ledger."""

    documents: ToolRegistry
    ledger: Ledger
    toolbox: Toolbox

    def cite(self, document: str, unit: Unit) -> str:
        """Register a row as evidence and return its ID."""
        return self.ledger.add(document, unit.locator, unit.text).id

    def answer(
        self,
        text: str,
        evidence: Iterable[str],
        actions: Iterable[Action] = (),
        reason: str = "Computed from the logs by rule; no model was called.",
    ) -> Answer:
        citations, _ = self.ledger.citations(list(evidence)[:30])
        return Answer(
            status="ok",
            answer=text,
            citations=citations,
            actions=list(actions),
            confidence=1.0,
            reason=reason,
        )


Solver = Callable[[Task, Workspace], Answer | None]


class Playbooks:
    def __init__(self) -> None:
        self._solvers: list[tuple[re.Pattern[str], Solver]] = []

    def register(self, pattern: str) -> Callable[[Solver], Solver]:
        """Decorator: offer the solver every task whose question matches the pattern."""

        def decorator(solver: Solver) -> Solver:
            self._solvers.append((re.compile(pattern, re.IGNORECASE | re.DOTALL), solver))
            return solver

        return decorator

    def solve(self, task: Task, workspace: Workspace) -> Answer | None:
        for pattern, solver in self._solvers:
            if not pattern.search(task.question):
                continue
            try:
                answer = solver(task, workspace)
            except Exception:
                # A solver that meets data it did not expect must not cost the task.
                continue
            if answer is not None:
                return answer
        return None


def load(reference: str) -> Playbooks:
    """Resolve "package.module:attribute" from the configuration."""
    module, _, attribute = reference.partition(":")
    playbooks = getattr(importlib.import_module(module), attribute)
    if not isinstance(playbooks, Playbooks):
        raise TypeError("agent.playbooks must name a hacklab.playbooks.Playbooks instance")
    return playbooks
