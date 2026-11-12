"""Who reached whom, and in what order: propagation through an agent swarm."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hacklab.forensics.events import parse_timestamp

MAX_EDGES = 200_000


def _earlier(candidate: datetime | None, current: datetime | None) -> bool:
    """None stands for "any time", which nothing can precede."""
    if current is None:
        return False
    return candidate is None or candidate < current


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    at: datetime | None = None
    ref: Any = field(default=None, compare=False)  # the log row behind this contact


class Graph:
    """Directed contacts such as messages, uploads or token use, optionally timed."""

    def __init__(self, edges: Iterable[Edge]):
        self.edges = list(edges)
        if len(self.edges) > MAX_EDGES:
            raise ValueError("Graph exceeds 200000 edges")
        self.out: dict[str, list[Edge]] = {}
        self.inbound: dict[str, list[Edge]] = {}
        for edge in self.edges:
            self.out.setdefault(edge.source, []).append(edge)
            self.inbound.setdefault(edge.target, []).append(edge)

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[Mapping[str, Any]],
        source: str,
        target: str,
        time: str | None = None,
    ) -> Graph:
        edges = []
        for row in rows:
            origin, destination = row.get(source), row.get(target)
            if origin in (None, "") or destination in (None, ""):
                continue
            moment = parse_timestamp(row.get(time)) if time else None
            edges.append(Edge(str(origin), str(destination), moment))
        return cls(edges)

    @property
    def nodes(self) -> list[str]:
        return sorted(set(self.out) | set(self.inbound))

    def reach(self, start: str) -> dict[str, tuple[datetime | None, Edge]]:
        """Everything `start` could have influenced: earliest possible time and contact.

        With timed edges only causal chains count: a contact spreads nothing
        that its sender received later. An untimed contact may have happened
        at any moment after its sender was reached.
        """
        arrival: dict[str, tuple[datetime | None, Edge | None]] = {start: (None, None)}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            arrived = arrival[node][0]
            for edge in self.out.get(node, []):
                if edge.target == start:
                    continue
                if arrived is not None and edge.at is not None and edge.at < arrived:
                    continue
                when = edge.at if edge.at is not None else arrived
                if edge.target not in arrival or _earlier(when, arrival[edge.target][0]):
                    arrival[edge.target] = (when, edge)
                    queue.append(edge.target)
        del arrival[start]
        ordered = sorted(
            arrival.items(), key=lambda item: (item[1][0] is None, item[1][0], item[0])
        )
        return {node: (when, edge) for node, (when, edge) in ordered if edge is not None}

    def origins(self, affected: Iterable[str]) -> list[str]:
        """Affected nodes that no other affected node contacted first: where it began."""
        group = set(affected)
        roots = []
        for node in sorted(group):
            inbound = [
                edge.at
                for edge in self.inbound.get(node, [])
                if edge.source in group and edge.source != node
            ]
            if not inbound:
                roots.append(node)
                continue
            timed = [moment for moment in inbound if moment is not None]
            sent = [
                edge.at
                for edge in self.out.get(node, [])
                if edge.at is not None and edge.target in group
            ]
            if len(timed) == len(inbound) and sent and min(sent) < min(timed):
                roots.append(node)
        return roots

    def path(self, start: str, goal: str) -> list[str]:
        """A shortest chain of contacts from start to goal, or an empty list."""
        previous: dict[str, str | None] = {start: None}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            if node == goal:
                chain = []
                step: str | None = node
                while step is not None:
                    chain.append(step)
                    step = previous[step]
                return chain[::-1]
            for edge in self.out.get(node, []):
                if edge.target not in previous:
                    previous[edge.target] = node
                    queue.append(edge.target)
        return []

    def degree(self) -> list[tuple[str, int, int]]:
        """(node, contacts sent, contacts received), busiest senders first."""
        rows = [
            (node, len(self.out.get(node, [])), len(self.inbound.get(node, [])))
            for node in self.nodes
        ]
        return sorted(rows, key=lambda row: (-row[1], -row[2], row[0]))
