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
from hacklab.embedding import Embedder, cosine, fuse
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
# A value that reads as encoded data or a digest rather than as a label.
_OPAQUE = re.compile(r"(?=.*[0-9])(?=.*[A-Za-z])[A-Za-z0-9+/=_]{16,}")
# Arguments of a mutating tool that explain the action instead of naming its target.
_FREE_TEXT = {"reason", "comment", "note", "justification", "message", "description"}
SIGNATURES = {
    "search": "search(query, limit=5): best-matching evidence lines across the task documents",
    "read": (
        "read(document, start=1, limit=30): consecutive lines of a prose document; "
        "for logs and tables use table or after"
    ),
    "table": (
        "table(document, operation=rows|count|sum|min|max|mean|group|distinct, column?, where?, "
        "limit=12): filter and aggregate rows of a CSV/JSONL/XLSX log. "
        'where={"action":"login","actor":["a","b"],"ts__gte":"2026-11-14T02:00",'
        '"src_ip__outside":"10.0.0.0/8"}; operators contains|prefix|ne|gt|gte|lt|lte|in|not_in|'
        "re|within|outside. distinct and group list the values of column"
    ),
    "after": (
        "after(document, key, first, where?, operation=rows|count|distinct|group, column?, "
        "limit=12): rows matching where whose key value already had a row matching first, "
        'i.e. what each actor did from its trigger event on. Example: key="actor", '
        'first={"action":"exec_payload"}, where={"action":"create_token"}, '
        'operation="distinct", column="target"'
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
RESULT_ID = re.compile(r"r\d{1,4}")


@dataclass(frozen=True)
class Result:
    """A list a tool computed. The final answer can use it whole, without copying."""

    values: tuple[str, ...]
    evidence: tuple[str, ...]


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


def split_items(text: str) -> list[str]:
    """The entries of a list written as prose, JSON or bullets, in order."""
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    parts = parsed if isinstance(parsed, list) else re.split(r"[,;\n]|\band\b", text)
    cleaned = (str(part).strip().strip("\"'`[]()*- .") for part in parts)
    return [part for part in cleaned if part]


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
        embedder: Embedder | None = None,
    ):
        self.embedder = embedder
        self._vectors: dict[str, list[float]] = {}
        self.documents = documents
        self.ledger = ledger
        self.config = config
        self.guard = ActionGuard(config.guard, ledger.grounded)
        self.extra = {spec.name: spec for spec in extra}
        self.builtin = [name for name in config.agent.tools if name in SIGNATURES]
        self.results: dict[str, Result] = {}
        # Numbers the tools computed, as written: an answer repeats them exactly.
        self.numbers: list[str] = []
        self.used = False  # whether any tool ran for this task
        # What each call returned, in brief: the facts a reviewer needs beside the quotes.
        self.log: list[str] = []
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

    def result(self, reference: Any) -> Result | None:
        match = RESULT_ID.search(str(reference).lower())
        return self.results.get(match.group()) if match else None

    def _listing(self, entries: list[tuple[str, str, str | None]]) -> list[str]:
        """Lines for a list of values and a result ID that stands for all of them.

        Only a preview is printed: a long list costs tokens on every later
        step, and the model should hand the list on by ID, not retype it.
        """
        if not entries:
            return ["(no values)"]
        name = f"r{len(self.results) + 1}"
        cited = tuple(evidence for _, _, evidence in entries if evidence)[:30]
        self.results[name] = Result(tuple(value for value, _, _ in entries), cited)
        self.ledger.ground(value for value, _, _ in entries)
        preview = self.config.agent.list_preview
        lines = [f"{value or '(empty)'} {extra}".rstrip() for value, extra, _ in entries[:preview]]
        if len(entries) > preview:
            lines.append(f"… {len(entries) - preview} more values, all kept in {name}")
        lines.append(f"-- {name} = these {len(entries)} values")
        return lines

    def _summarize(
        self, document: str, units: list[Unit], operation: str, column: Any, limit: Any
    ) -> Observation:
        """rows, count, distinct or group over units that a filter already selected."""
        limit = limit if isinstance(limit, int) and 1 <= limit <= 100 else 12

        def cite(unit: Unit) -> Evidence:
            row = {"document": document, "locator": unit.locator, "quote": unit.text[:2000]}
            return self._evidence(row)

        if operation == "rows":
            lines = [cite(unit).line() for unit in units[:limit]]
            note = f"-- matched_rows={len(units)}" + (
                " truncated=yes" if len(units) > limit else ""
            )
            return Observation(lines + [note])
        if operation == "count":
            self.numbers.append(str(len(units)))
            lines = [f"value={len(units)} operation=count matched_rows={len(units)}"]
            return Observation(lines + [cite(unit).line() for unit in units[:3]])
        if operation not in {"distinct", "group"}:
            return Observation(error="operation must be rows, count, distinct or group")
        if column not in self.documents.columns(document):
            names = ", ".join(self.documents.columns(document)[:40])
            return Observation(error=f"{operation} needs column, one of: {names}")
        first: dict[str, Unit] = {}
        counts: dict[str, int] = {}
        for unit in units:
            value = unit.values.get(column)
            text = "" if value is None else str(value)
            first.setdefault(text, unit)
            counts[text] = counts.get(text, 0) + 1
        ranked = sorted(counts, key=lambda value: (-counts[value], value))
        entries = [
            (
                value,
                f"count={counts[value]}",
                cite(first[value]).id if position < 30 else None,
            )
            for position, value in enumerate(ranked)
        ]
        return Observation(self._listing(entries) + [f"-- matched_rows={len(units)}"])

    def _after(self, arguments: dict[str, Any]) -> Observation:
        document = str(arguments.get("document", ""))
        columns = self.documents.columns(document)
        key = str(arguments.get("key", ""))
        time = arguments.get("time") or time_field(columns)
        if key not in columns:
            return Observation(error=f"key must be a column: {', '.join(columns[:40])}")
        if not time or time not in columns:
            return Observation(error="no time column found; pass time")
        if not arguments.get("first"):
            return Observation(error="first must be a filter for the trigger rows")
        started: dict[str, Any] = {}
        for unit in self.documents.rows(document, arguments["first"]):
            moment = parse_timestamp(unit.values.get(time))
            who = unit.values.get(key)
            if moment is None or who in (None, ""):
                continue
            who = str(who)
            started[who] = min(started[who], moment) if who in started else moment
        selected = []
        for unit in self.documents.rows(document, arguments.get("where")):
            moment = parse_timestamp(unit.values.get(time))
            begin = started.get(str(unit.values.get(key)))
            if moment is not None and begin is not None and moment >= begin:
                selected.append(unit)
        observation = self._summarize(
            document,
            selected,
            str(arguments.get("operation", "rows")),
            arguments.get("column"),
            arguments.get("limit"),
        )
        observation.lines.append(f"-- {len(started)} {key} values had a trigger row")
        return observation

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
            self.numbers.append(str(result["value"]))
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

    def _channel(self, document: str, wanted: str, keys: list[str]) -> list[str]:
        """Decode the numbered chunks of one channel of a tabular document."""
        units = self.documents.rows(document)
        lines = []
        for stream in decoding.reassemble((unit.values for unit in units), keys):
            if stream.channel != wanted:
                continue
            first = next(unit for unit in units if unit.values is stream.sources[0])
            lines.append(
                self._decoded_line(document, first.locator, first.text, stream.decoded)
                + f" parts={stream.parts} checksum={stream.checksum}"
                + (f" missing={list(stream.missing)}" if stream.missing else "")
            )
        return lines

    def _chunk_at(self, document: str, locator: str) -> tuple[str, str] | None:
        """The document and channel if that row is one numbered chunk of a series."""
        if document.startswith("tool:") or not self.documents.columns(document):
            return None
        unit = next((u for u in self.documents.units(document) if u.locator == locator), None)
        if unit is None or unit.values is None:
            return None
        row = {str(key).lower(): value for key, value in unit.values.items()}
        numbered = any(row.get(key) not in (None, "") for key in ("seq", "sequence", "part"))
        channel = next((row[k] for k in ("channel", "chan", "ch", "stream") if row.get(k)), None)
        return (document, str(channel)) if numbered and channel is not None else None

    def _chunk_of(self, text: str) -> tuple[str, str] | None:
        """The document and channel in which this text is one numbered chunk, if any."""
        needle = text.strip()
        if len(needle) < 8:
            return None
        for entry in self.documents.inventory():
            if not entry.get("columns"):
                continue
            for unit in self.documents.units(entry["document"]):
                if unit.values is None or needle not in unit.text:
                    continue
                row = {str(key).lower(): value for key, value in unit.values.items()}
                payload = next(
                    (row[k] for k in ("payload", "data", "body", "chunk", "blob") if row.get(k)),
                    None,
                )
                channel = next(
                    (row[k] for k in ("channel", "chan", "ch", "stream") if row.get(k)), None
                )
                if payload is not None and channel is not None and needle in str(payload):
                    return entry["document"], str(channel)
        return None

    def _decode(self, arguments: dict[str, Any]) -> Observation:
        keys = self._keys(arguments.get("key"))
        if arguments.get("document") and "channel" in arguments:
            wanted = str(arguments["channel"])
            lines = self._channel(str(arguments["document"]), wanted, keys)
            return Observation(lines or [f"no numbered chunks on channel {wanted!r}"])
        if arguments.get("evidence"):
            # One ID or several, in whatever punctuation the model used.
            found = [
                self.ledger.get(part)
                for part in re.findall(r"e\d+", str(arguments["evidence"]).lower())
            ]
            rows = [evidence for evidence in found if evidence is not None]
            if not rows:
                return Observation(error="Unknown evidence ID")
            lines = []
            channels: set[tuple[str, str]] = set()
            for evidence in rows:
                chunk = self._chunk_at(evidence.source, evidence.locator)
                if chunk is not None:
                    channels.add(chunk)
                    continue
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
                if best is not None and best.layers:
                    lines.append(
                        self._decoded_line(evidence.source, evidence.locator, evidence.quote, best)
                    )
            for document, channel in sorted(channels):
                # A chunk alone is noise: decode the whole series it belongs to.
                lines += self._channel(document, channel, keys)
            return Observation(lines or ["nothing to decode in that evidence"])
        if isinstance(arguments.get("text"), str):
            decoded = decoding.peel(arguments["text"], keys)
            if not decoded.layers and (chunk := self._chunk_of(arguments["text"])) is not None:
                lines = self._channel(chunk[0], chunk[1], keys)
                if lines:
                    return Observation(
                        [f"one chunk of channel {chunk[1]} in {chunk[0]}; whole series:"] + lines
                    )
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
            entries = [
                (
                    name,
                    f"at={when.isoformat() if when else 'unknown'}"
                    + (f" via=[{cite(edge)}]" if position < 30 else ""),
                    cite(edge) if position < 30 else None,
                )
                for position, (name, (when, edge)) in enumerate(reached.items())
            ]
            return Observation([f"-- reached={len(reached)} from={node}"] + self._listing(entries))
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
        observation = self._execute(name, arguments)
        repeated = bool(observation.lines) and observation.lines[0].startswith("repeated call")
        if observation.error is None and not repeated:
            call = json.dumps(arguments, ensure_ascii=False, default=str)[:300]
            self.log.append(f"{name}({call}) -> {observation.text(500)}")
        return observation

    def _execute(self, name: str, arguments: dict[str, Any]) -> Observation:
        self._calls += 1
        if name not in self.names():
            return Observation(error=f"Unknown tool; available: {', '.join(self.names())}")
        self.used = True
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
            if name == "table" and arguments.get("operation") in {"group", "distinct"}:
                document = str(arguments.get("document", ""))
                units = self.documents.rows(document, arguments.get("where"))
                return self._summarize(
                    document, units, arguments["operation"], arguments.get("column"), None
                )
            if name == "search":
                tidy = self._tidy(name, arguments)
                found = self.render(self.search_rows(tidy.get("query", ""), tidy.get("limit", 5)))
                found.lines += self._search_hints(str(arguments.get("query", "")))
                return found
            if name in {"read", "table", "calculate"}:
                return self.render(self.documents.execute(name, self._tidy(name, arguments)))
            handler = {
                "decode": self._decode,
                "iocs": self._iocs,
                "graph": self._graph,
                "after": self._after,
            }.get(name, self._phases)
            return handler(arguments)
        except ToolError as exc:
            return Observation(error=str(exc))
        except decoding.DecodeError as exc:
            return Observation(error=str(exc))
        except Exception:
            # Organizer tools and hostile inputs must not end the task.
            return Observation(error="The tool failed on these arguments")

    def search_rows(self, query: Any, limit: Any) -> dict[str, Any]:
        """Keyword search, re-ranked by meaning when an embedder is configured.

        The candidates are the best keyword hits plus every line of short
        prose documents, so a paraphrase without a shared word can still be
        found. Any failure of the embedder leaves the keyword ranking.
        """
        if self.embedder is None:
            return self.documents.execute("search", {"query": query, "limit": limit})
        pool = self.config.embedding.candidates
        found = self.documents.execute("search", {"query": query, "limit": pool})
        if "error" in found or not isinstance(limit, int):
            return found if "error" in found else {"error": "limit must be a number"}
        rows = list(found["results"])
        lexical = list(range(len(rows)))
        seen = {(row["document"], row["locator"]) for row in rows}
        for entry in self.documents.inventory():
            if entry.get("columns") or "error" in entry or entry["units"] > 200:
                continue
            for unit in self.documents.units(entry["document"]):
                key = (entry["document"], unit.locator)
                if unit.text.strip() and key not in seen and len(rows) < 300:
                    rows.append({"document": key[0], "locator": key[1], "quote": unit.text[:2000]})
        try:
            texts = (row["quote"][:1000] for row in rows)
            new = list(dict.fromkeys(text for text in texts if text not in self._vectors))
            for text, vector in zip(new, self.embedder.encode(new), strict=True):
                self._vectors[text] = vector
            target = self.embedder.encode([str(query)], query=True)[0]
            semantic = sorted(
                range(len(rows)),
                key=lambda index: (
                    -cosine(target, self._vectors[rows[index]["quote"][:1000]]),
                    index,
                ),
            )
        except Exception:
            # Retrieval quality may drop; the task must not.
            found["results"] = found["results"][:limit]
            return found
        order = fuse(lexical, semantic, self.config.embedding.weight)
        found["results"] = [rows[index] for index in order[:limit]]
        found["truncated"] = len(rows) > limit
        return found

    def describe_table(self, document: str) -> list[str]:
        """One line per column of a table: its frequent values, or how many it has."""
        lines = []
        for column, entry in self.documents.profile(document).items():
            if "range" in entry:
                lines.append(f"    {column}: {entry['range'][0]} to {entry['range'][1]}")
            elif (
                "values" in entry
                and entry["distinct"] <= 12
                and not any(
                    len(value) > 32 or _OPAQUE.fullmatch(value) for value in entry["values"]
                )
            ):
                # Short labels such as action names; never blobs or free text.
                shown = ", ".join(
                    f"{value or '(empty)'} {count}" for value, count in entry["values"].items()
                )
                lines.append(f"    {column}: {shown}"[:400])
            else:
                lines.append(f"    {column}: {entry['distinct']} distinct values")
        return lines

    def _search_hints(self, query: str) -> list[str]:
        """Point from a ranked text search to the exact filter, when the query names a value.

        Search shows the best-ranked lines, not all matches. A model that
        searches a log for an action name must learn that a complete, ordered
        answer comes from the table.
        """
        terms = {term.casefold() for term in re.findall(r"[\w.:/-]+", query) if len(term) > 2}
        hints = []
        for entry in self.documents.inventory():
            if not entry.get("columns"):
                continue
            for column, facts in self.documents.profile(entry["document"]).items():
                for value, count in facts.get("values", {}).items():
                    if value.casefold() in terms and len(hints) < 3:
                        hints.append(
                            f"-- search shows a sample. {entry['document']} has {count} rows with "
                            f'{column}={value}: table(document="{entry["document"]}", '
                            f'where={{"{column}":"{value}"}}) lists all of them in time order'
                        )
        return hints

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
