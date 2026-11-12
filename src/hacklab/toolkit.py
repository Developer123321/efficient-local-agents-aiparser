"""One compact tool surface over documents, forensics and the organizers' own tools.

Tool descriptions are part of every prompt, so each is one line. Results are
plain lines with evidence IDs rather than nested JSON: fewer tokens, and closer
to the command output a model has seen most.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from hacklab.config import Config
from hacklab.evidence import Evidence, Ledger
from hacklab.forensics import decode as decoding
from hacklab.forensics import iocs as indicators
from hacklab.forensics import phases as killchain
from hacklab.forensics.events import flatten, parse_timestamp, time_field
from hacklab.forensics.graph import Edge, Graph
from hacklab.guard import ActionGuard
from hacklab.models import Action
from hacklab.tools import ToolError, ToolRegistry, Unit

MAX_LINES = 60
MAX_DECODED_CHARS = 1500
_ENCODED = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
# Arguments of a mutating tool that explain the action instead of naming its target.
_FREE_TEXT = {"reason", "comment", "note", "justification", "message", "description"}
SIGNATURES = {
    "search": "search(query, limit=5): best-matching evidence lines across the task documents",
    "read": "read(document, start=1, limit=40): consecutive lines, rows or pages of one document",
    "table": (
        "table(document, operation=rows|count|sum|min|max|mean|group|distinct, column?, where?, "
        'limit=20): filter and aggregate rows of a CSV/JSONL/XLSX; where={"col":"v","col__gt":5}, '
        "operators contains|prefix|suffix|ne|gt|gte|lt|lte|in|re"
    ),
    "calculate": "calculate(expression): exact decimal arithmetic",
    "decode": (
        "decode(evidence? | text? | document+channel?, key?): remove base64/hex/gzip/XOR layers; "
        "with document+channel, join that channel's numbered chunks first"
    ),
    "iocs": (
        "iocs(document? | evidence? | text?, kinds?): tokens, keys, addresses, domains and "
        "hashes found by pattern, with counts"
    ),
    "graph": (
        "graph(document, source, target, operation=reach|origins|path|degree, node?, goal?, "
        "nodes?, time?, where?): who contacted whom; reach lists everything node could have "
        "influenced in time order"
    ),
    "phases": "phases(document, column, time?, where?): rows per kill-chain phase, by rule",
}


@dataclass(frozen=True)
class ToolSpec:
    """A tool supplied from outside, such as one from the organizers' starter kit."""

    name: str
    signature: str
    handler: Callable[..., Any]
    # A mutating tool changes the environment (revoke, quarantine, delete). It
    # passes the action guard first and is never served from the repeat memo.
    mutating: bool = False

    @classmethod
    def from_function(
        cls, function: Callable[..., Any], *, mutating: bool = False, name: str | None = None
    ) -> ToolSpec:
        """Describe a Python function by its signature and the first line of its docstring."""
        label = name or function.__name__
        parameters = []
        for parameter in inspect.signature(function).parameters.values():
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                continue
            optional = parameter.default is not parameter.empty
            parameters.append(
                f"{parameter.name}={parameter.default!r}" if optional else parameter.name
            )
        summary = (inspect.getdoc(function) or "").split("\n", 1)[0].strip()
        signature = f"{label}({', '.join(parameters)})" + (f": {summary}" if summary else "")
        return cls(label, signature[:400], function, mutating)

    @classmethod
    def from_schema(
        cls,
        schema: dict[str, Any],
        call: Callable[[str, dict[str, Any]], Any],
        *,
        mutating: bool = False,
    ) -> ToolSpec:
        """Adapt an OpenAI-style function schema whose calls go through one dispatcher."""
        function = schema.get("function", schema)
        name = str(function["name"])
        parameters = function.get("parameters", {})
        required = set(parameters.get("required", []))
        names = [key if key in required else f"{key}?" for key in parameters.get("properties", {})]
        summary = str(function.get("description", "")).split("\n", 1)[0].strip()
        signature = f"{name}({', '.join(names)})" + (f": {summary}" if summary else "")
        return cls(name, signature[:400], lambda **arguments: call(name, arguments), mutating)


@dataclass
class Observation:
    lines: list[str] = field(default_factory=list)
    error: str | None = None

    def text(self, allowance: int) -> str:
        """The observation within a character allowance; omitted lines are counted."""
        if self.error is not None:
            return f"error: {self.error}"[:allowance]
        kept: list[str] = []
        size = 0
        for line in self.lines:
            if size + len(line) + 1 > allowance - 40 and kept:
                kept.append(f"… {len(self.lines) - len(kept)} more lines omitted; narrow the call")
                break
            kept.append(line[: max(allowance - 40, 80)])
            size += len(line) + 1
        return "\n".join(kept) if kept else "(no results)"


def logfmt(value: Any) -> str:
    """A flat record as key=value pairs, a nested or scalar one as compact JSON."""
    if isinstance(value, dict) and all(
        not isinstance(item, (dict, list)) for item in value.values()
    ):
        parts = []
        for key, item in value.items():
            text = "" if item is None else str(item)
            parts.append(f'{key}="{text}"' if " " in text or not text else f"{key}={text}")
        return " ".join(parts)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def to_rows(result: Any) -> list[Any]:
    """The list inside an arbitrary tool result, so that every item becomes one evidence row."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        lists = [value for value in result.values() if isinstance(value, list)]
        if len(lists) == 1 and lists[0]:
            return lists[0]
        return [result]
    if isinstance(result, str):
        return [line for line in result.splitlines() if line.strip()] or [result]
    return [result]


class Toolbox:
    def __init__(
        self,
        documents: ToolRegistry,
        ledger: Ledger,
        config: Config,
        extra: Iterable[ToolSpec] = (),
    ):
        self.documents = documents
        self.ledger = ledger
        self.config = config
        self.guard = ActionGuard(config.guard, ledger.grounded)
        self.extra = {spec.name: spec for spec in extra}
        self.builtin = [name for name in config.agent.tools if name in SIGNATURES]
        self._memo: dict[str, int] = {}
        self._calls = 0
        self._scanned_keys = False

    def names(self) -> list[str]:
        return self.builtin + [name for name in self.extra if name not in self.builtin]

    def describe(self) -> str:
        lines = [SIGNATURES[name] for name in self.builtin]
        lines += [spec.signature for name, spec in self.extra.items() if name not in self.builtin]
        return "\n".join(lines)

    # -- rendering ---------------------------------------------------------

    def _evidence(self, row: dict[str, Any]) -> Evidence:
        """Register a row; a JSON record is shown as key=value pairs, which costs fewer tokens."""
        quote = row["quote"]
        derived = None
        if quote.startswith("{") and len(quote) < 2000:
            try:
                record = json.loads(quote)
            except ValueError:
                record = None
            if isinstance(record, dict):
                derived = logfmt(flatten(record))
        return self.ledger.add(row["document"], row["locator"], quote, derived)

    def render(self, result: dict[str, Any]) -> Observation:
        """A document tool's result as lines with evidence IDs."""
        if "error" in result:
            return Observation(error=str(result["error"]))
        lines: list[str] = []
        if "value" in result:
            facts = {
                key: result[key]
                for key in ("value", "operation", "column", "matched_rows", "number_format")
                if result.get(key) is not None
            }
            if result.get("ambiguous_numbers"):
                facts["ambiguous_numbers"] = result["ambiguous_numbers"]
            lines.append(logfmt(facts))
            lines += [self._evidence(row).line() for row in result.get("citations", [])[:5]]
        for row in result.get("results", []) + result.get("rows", []):
            lines.append(self._evidence(row).line())
        for group in result.get("groups", []):
            evidence = self._evidence(group)
            lines.append(
                f"{group['value'] or '(empty)'} count={group['count']} first=[{evidence.id}]"
            )
        notes = {
            key: result[key]
            for key in (
                "matched_rows",
                "matched_units",
                "distinct_values",
                "total_units",
                "next_start",
            )
            if result.get(key) is not None and "value" not in result
        }
        if result.get("truncated"):
            notes["truncated"] = "yes"
        if result.get("coverage") == "partial":
            notes["coverage"] = "partial"
        if notes:
            lines.append("-- " + logfmt(notes))
        return Observation(lines)

    # -- forensic tools ----------------------------------------------------

    def _keys(self, extra: Any = None) -> list[str]:
        """Key hints from evidence so far and, once, from every task document."""
        if not self._scanned_keys:
            self._scanned_keys = True
            for entry in self.documents.inventory():
                if "error" in entry:
                    continue
                text = "\n".join(unit.text for unit in self.documents.units(entry["document"]))
                for key in decoding.candidate_keys(text):
                    if key not in self.ledger.compactor.keys:
                        self.ledger.compactor.keys.append(key)
        keys = self.ledger.keys
        return ([str(extra)] if extra else []) + keys

    def _decode(self, arguments: dict[str, Any]) -> Observation:
        keys = self._keys(arguments.get("key"))
        if arguments.get("document") and "channel" in arguments:
            document = str(arguments["document"])
            units = self.documents.rows(document)
            wanted = str(arguments["channel"])
            streams = decoding.reassemble((unit.values for unit in units), keys)
            lines = []
            for stream in streams:
                if stream.channel != wanted:
                    continue
                first = next(unit for unit in units if unit.values is stream.sources[0])
                lines.append(
                    self._decoded_line(document, first.locator, first.text, stream.decoded)
                    + f" parts={stream.parts} checksum={stream.checksum}"
                    + (f" missing={list(stream.missing)}" if stream.missing else "")
                )
            return Observation(lines or [f"no numbered chunks on channel {wanted!r}"])
        if arguments.get("evidence"):
            evidence = self.ledger.get(str(arguments["evidence"]))
            if evidence is None:
                return Observation(error="Unknown evidence ID")
            blobs = [evidence.quote] + _ENCODED.findall(evidence.quote)
            best = max(
                (decoding.peel(blob, keys) for blob in blobs if len(blob) >= 16),
                key=lambda decoded: (
                    decoded.text is not None,
                    len(decoded.layers),
                    len(decoded.data),
                ),
                default=None,
            )
            if best is None or not best.layers:
                return Observation(["nothing to decode in that evidence"])
            return Observation(
                [self._decoded_line(evidence.source, evidence.locator, evidence.quote, best)]
            )
        if isinstance(arguments.get("text"), str):
            decoded = decoding.peel(arguments["text"], keys)
            body = (
                decoded.text if decoded.text is not None else f"<{len(decoded.data)} binary bytes>"
            )
            shown = self.ledger.compactor.compact(body[:MAX_DECODED_CHARS])
            return Observation([f"layers={'>'.join(decoded.layers) or 'none'} | {shown}"])
        return Observation(error="decode needs evidence, text, or document with channel")

    def _decoded_line(
        self, source: str, locator: str, quote: str, decoded: decoding.Decoded
    ) -> str:
        body = decoded.text if decoded.text is not None else f"<{len(decoded.data)} binary bytes>"
        derived = f"decoded via {'>'.join(decoded.layers) or 'none'}: {body[:MAX_DECODED_CHARS]}"
        return self.ledger.add(source, locator, quote, derived).line()

    def _units(self, arguments: dict[str, Any]) -> list[Unit]:
        return self.documents.rows(str(arguments.get("document", "")), arguments.get("where"))

    def _iocs(self, arguments: dict[str, Any]) -> Observation:
        if arguments.get("document"):
            units = self.documents.units(str(arguments["document"]))
            sources = [(str(arguments["document"]), unit.locator, unit.text) for unit in units]
        elif arguments.get("evidence"):
            evidence = self.ledger.get(str(arguments["evidence"]))
            if evidence is None:
                return Observation(error="Unknown evidence ID")
            sources = [(evidence.source, evidence.locator, evidence.quote)]
        elif isinstance(arguments.get("text"), str):
            sources = [("", "", arguments["text"])]
        else:
            return Observation(error="iocs needs document, evidence or text")
        kinds = arguments.get("kinds")
        wanted = {str(kind) for kind in kinds} if isinstance(kinds, list) else None
        counts: dict[tuple[str, str], int] = {}
        first: dict[tuple[str, str], tuple[str, str, str]] = {}
        for source, locator, text in sources:
            for kind, values in indicators.extract(text).items():
                if wanted is not None and kind not in wanted:
                    continue
                for value in values:
                    counts[(kind, value)] = counts.get((kind, value), 0) + 1
                    first.setdefault((kind, value), (source, locator, text))
        lines = []
        ranked = sorted(counts.items(), key=lambda item: (item[0][0], -item[1], item[0][1]))
        for (kind, value), count in ranked[:MAX_LINES]:
            shown = (
                self.ledger.compactor.compact(value) if self.config.guard.compact_secrets else value
            )
            line = f"{kind} {shown} rows={count}"
            if kind == "ipv4":
                line += f" class={indicators.classify_ip(value)}"
            source, locator, text = first[(kind, value)]
            if source:
                line += f" first=[{self.ledger.add(source, locator, text).id}]"
            lines.append(line)
        if len(ranked) > MAX_LINES:
            lines.append(f"-- {len(ranked) - MAX_LINES} more indicators omitted; pass kinds")
        return Observation(lines or ["no indicators found"])

    def _graph(self, arguments: dict[str, Any]) -> Observation:
        document = str(arguments.get("document", ""))
        units = self._units(arguments)
        source, target = str(arguments.get("source", "")), str(arguments.get("target", ""))
        columns = self.documents.columns(document)
        if source not in columns or target not in columns:
            return Observation(
                error=f"source and target must be columns: {', '.join(columns[:40])}"
            )
        time = arguments.get("time") or time_field(columns)
        edges = [
            Edge(
                str(unit.values[source]),
                str(unit.values[target]),
                parse_timestamp(unit.values.get(time)) if time else None,
                unit,
            )
            for unit in units
            if unit.values.get(source) not in (None, "")
            and unit.values.get(target) not in (None, "")
        ]
        graph = Graph(edges)

        def cite(edge: Edge) -> str:
            return self.ledger.add(document, edge.ref.locator, edge.ref.text[:2000]).id

        operation = str(arguments.get("operation", "reach"))
        if operation == "reach":
            node = str(arguments.get("node", ""))
            if node not in graph.nodes:
                return Observation(error="node does not occur in source or target")
            reached = graph.reach(node)
            lines = [
                f"{name} at={when.isoformat() if when else 'unknown'} via=[{cite(edge)}]"
                for name, (when, edge) in list(reached.items())[:MAX_LINES]
            ]
            if len(reached) > MAX_LINES:
                lines.append(
                    f"-- {len(reached) - MAX_LINES} later nodes omitted; narrow with where"
                )
            return Observation([f"-- reached={len(reached)} from={node}"] + lines)
        if operation == "origins":
            nodes = arguments.get("nodes")
            group = [str(node) for node in nodes] if isinstance(nodes, list) else list(graph.out)
            return Observation([f"origin {node}" for node in graph.origins(group)] or ["no origin"])
        if operation == "path":
            chain = graph.path(str(arguments.get("node", "")), str(arguments.get("goal", "")))
            return Observation([" -> ".join(chain)] if chain else ["no path"])
        if operation == "degree":
            return Observation(
                [
                    f"{node} sent={sent} received={received}"
                    for node, sent, received in graph.degree()
                ]
            )
        return Observation(error="operation must be reach, origins, path or degree")

    def _phases(self, arguments: dict[str, Any]) -> Observation:
        units = self._units(arguments)
        column = str(arguments.get("column", ""))
        document = str(arguments.get("document", ""))
        columns = self.documents.columns(document)
        if column not in columns:
            return Observation(error=f"column must be one of: {', '.join(columns[:40])}")
        time = arguments.get("time") or time_field(columns)
        summary = killchain.summarize((unit.values for unit in units), column, time)
        return Observation(
            [
                logfmt(
                    {
                        "phase": entry["phase"],
                        "rows": entry["count"],
                        "first": entry["first"].isoformat() if entry["first"] else None,
                        "last": entry["last"].isoformat() if entry["last"] else None,
                    }
                )
                for entry in summary
            ]
        )

    # -- dispatch ----------------------------------------------------------

    def execute(self, name: str, arguments: dict[str, Any]) -> Observation:
        """Run one tool. Never raises: a failure is an observation the model can react to."""
        self._calls += 1
        if name not in self.names():
            return Observation(error=f"Unknown tool; available: {', '.join(self.names())}")
        arguments = self.ledger.compactor.expand(arguments)
        spec = self.extra.get(name) if name not in self.builtin else None
        try:
            memo = json.dumps([name, arguments], sort_keys=True, default=str)
        except (TypeError, ValueError):
            return Observation(error="Arguments must be plain JSON values")
        if not (spec and spec.mutating):
            if memo in self._memo:
                return Observation(
                    [
                        f"repeated call: identical to call {self._memo[memo]}, whose result "
                        "is unchanged. Answer with final or change the arguments."
                    ]
                )
            self._memo[memo] = self._calls
        try:
            if spec is not None:
                return self._external(spec, arguments)
            if name in {"search", "read", "table", "calculate"}:
                return self.render(self.documents.execute(name, self._tidy(name, arguments)))
            handler = {"decode": self._decode, "iocs": self._iocs, "graph": self._graph}.get(
                name, self._phases
            )
            return handler(arguments)
        except ToolError as exc:
            return Observation(error=str(exc))
        except decoding.DecodeError as exc:
            return Observation(error=str(exc))
        except Exception:
            # Organizer tools and hostile inputs must not end the task.
            return Observation(error="The tool failed on these arguments")

    def _tidy(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Repair what a small model commonly gets slightly wrong in tool arguments.

        Unknown keys are dropped, digit strings become numbers for limit and
        start, and a filter written as JSON text becomes an object. Each of
        these would otherwise cost a paid turn for an error message.
        """
        tidy = {
            key: value
            for key, value in arguments.items()
            if key in self.documents.argument_names(name)
        }
        for key in ("limit", "start"):
            if isinstance(tidy.get(key), str) and tidy[key].isdigit():
                tidy[key] = int(tidy[key])
        if isinstance(tidy.get("where"), str):
            try:
                tidy["where"] = json.loads(tidy["where"])
            except ValueError:
                pass
        return tidy

    def _external(self, spec: ToolSpec, arguments: dict[str, Any]) -> Observation:
        accepted: list[Action] = []
        if spec.mutating:
            # Identifier-like arguments are the targets; prose such as a reason is not.
            targets = [
                value
                for key, value in arguments.items()
                if isinstance(value, str)
                and key.casefold() not in _FREE_TEXT
                and value.strip()
                and " " not in value.strip()
            ]
            actions = [Action(type=spec.name, target=target) for target in targets[:5]]
            if not actions:
                # A step without a target, such as closing an endpoint: only
                # its type and the action count can be checked.
                actions = [Action(type=spec.name, target=spec.name)]
            accepted, problems = self.guard.check(actions, targeted=bool(targets))
            if problems or not accepted:
                return Observation(error=f"action refused: {(problems or ['already_done'])[0]}")
        try:
            result = spec.handler(**arguments)
        except TypeError:
            return Observation(error=f"Arguments do not match {spec.signature.split(':')[0]}")
        # Counted only once the tool has run: a failed call may be retried.
        self.guard.commit(accepted)
        rows = to_rows(result)
        lines = [
            self.ledger.add(
                f"tool:{spec.name}", f"call:{self._calls}:item:{index}", logfmt(row)
            ).line()
            for index, row in enumerate(rows[:MAX_LINES], 1)
            if logfmt(row)
        ]
        if len(rows) > MAX_LINES:
            lines.append(f"-- {len(rows) - MAX_LINES} more items omitted")
        return Observation(lines or ["(empty result)"])
