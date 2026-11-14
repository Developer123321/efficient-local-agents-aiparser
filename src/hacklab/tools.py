"""Deterministic, bounded document tools. These tools do not execute document instructions.

The path boundary is a data-access policy, not an operating-system sandbox. Document
parsers still run in this process; use the VM for untrusted competition inputs.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import math
import re
import struct
import threading
import zipfile
from collections import Counter, OrderedDict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal, InvalidOperation, localcontext
from email import message_from_bytes, policy
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree

from pydantic import Field, ValidationError

from hacklab.forensics.events import (
    count_by,
    flatten,
    matches,
    normalize_where,
    parse_timestamp,
    time_field,
    where_column,
)
from hacklab.models import Citation, StrictModel

# Incident logs run to tens of thousands of rows; the limits bound memory, not ambition.
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_DOCUMENT_CHARS = 30_000_000
MAX_UNITS = 200_000
MAX_SEARCH_CHARS = 60_000_000
MAX_SEARCH_UNITS = 400_000
MAX_RESPONSE_CHARS = 24_000
MAX_QUOTE_CHARS = 2000
MAX_COLUMNS = 100
MAX_DOCUMENTS = 2000
# Parsed documents are kept across tasks: a run asks many questions about the
# same logs. Bounded by the rows held, oldest out first.
MAX_SHARED_UNITS = 2_000_000
TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".log",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".xml",
    ".py",
    ".sh",
    ".sql",
}
SUPPORTED = TEXT_SUFFIXES | {
    ".json",
    ".jsonl",
    ".ndjson",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
    ".eml",
    ".pdf",
    ".docx",
    ".xlsx",
    ".odt",
    ".ods",
}
# Pictures are no documents: they are neither parsed nor searched. With the
# image tool in `[agent] tools` the model can be shown one, within these bounds.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
MAX_IMAGE_BYTES = 4_000_000
MAX_IMAGE_SIDE = 16_384
MAX_IMAGES = 200
# A log under these names is often structured: one JSON object or one run of
# key=value pairs per line. Such a file is read as a table.
SNIFFED_SUFFIXES = {".log", ".txt", ".out", ""}
_LOGFMT = re.compile(r"""([A-Za-z_@][\w.@/-]*)=("(?:[^"\\]|\\.)*"|[^\s"]*)""")
_CURRENCY = re.compile(r"(?i)eur|usd|chf|gbp|[€$£%]")
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}


class ToolError(ValueError):
    """A bounded, user-facing rejection."""


class SearchArgs(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20, strict=True)


class ReadArgs(StrictModel):
    document: str = Field(min_length=1, max_length=1000)
    start: int = Field(default=1, ge=1, le=MAX_UNITS, strict=True)
    limit: int = Field(default=30, ge=1, le=100, strict=True)


class CalculateArgs(StrictModel):
    expression: str = Field(min_length=1, max_length=500)


class TableArgs(StrictModel):
    document: str = Field(min_length=1, max_length=1000)
    operation: Literal["rows", "count", "sum", "min", "max", "mean", "group", "distinct"] = "rows"
    column: str | None = Field(default=None, max_length=200)
    # {"col": "v"} tests equality; {"col__gt": 5} uses an operator. The shapes
    # models write instead are accepted, see events.normalize_where.
    where: dict[str, Any] | str | None = None
    limit: int = Field(default=12, ge=1, le=100, strict=True)


@dataclass
class Unit:
    locator: str
    text: str
    values: dict[str, Any] | None = None


@dataclass
class Document:
    name: str
    units: list[Unit]
    digest: str
    columns: list[str] = field(default_factory=list)
    # Filled on first use for tables: rows in time order, what each column
    # holds, and which column carries the time of a row.
    ordered: list[Unit] | None = None
    profile: dict[str, dict[str, Any]] | None = None
    time: str | None = None
    timed: bool = False


_SHARED: OrderedDict[tuple[str, str, str], Document] = OrderedDict()
_SHARED_LOCK = threading.Lock()


def _shared(key: tuple[str, str, str], document: Document | None = None) -> Document | None:
    """Look up or store a parsed document by path, content digest and name."""
    with _SHARED_LOCK:
        if document is None:
            found = _SHARED.get(key)
            if found is not None:
                _SHARED.move_to_end(key)
            return found
        _SHARED[key] = document
        while len(_SHARED) > 1 and sum(len(d.units) for d in _SHARED.values()) > MAX_SHARED_UNITS:
            _SHARED.popitem(last=False)
        return document


def _looks_like_text(data: bytes) -> bool:
    """No NUL byte in the first kilobytes: a log without a telling suffix, not a binary."""
    return b"\x00" not in data[:8192]


def list_documents(root: Path, limit: int = MAX_DOCUMENTS) -> list[str]:
    """Every readable document below `root` as a relative name, in name order.

    Hidden files and folders are left out. At most `limit` names are returned
    and at most ten times as many entries visited: a huge tree is cut short,
    never an error.
    """
    base = root.resolve()
    names = []
    for visited, path in enumerate(base.rglob("*")):
        if visited >= limit * 10 or len(names) >= limit:
            break
        relative = path.relative_to(base)
        if any(part.startswith(".") for part in relative.parts) or not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED:
            try:
                with path.open("rb") as stream:
                    if not _looks_like_text(stream.read(8192)):
                        continue
            except OSError:
                continue
        names.append(relative.as_posix())
    return sorted(names)


def list_images(root: Path, limit: int = MAX_IMAGES) -> list[str]:
    """Every PNG and JPEG below `root` as a relative name, in name order, hidden ones left out."""
    base = root.resolve()
    names = []
    for visited, path in enumerate(base.rglob("*")):
        if visited >= MAX_DOCUMENTS * 10 or len(names) >= limit:
            break
        relative = path.relative_to(base)
        if (
            path.suffix.lower() in IMAGE_TYPES
            and path.is_file()
            and not any(part.startswith(".") for part in relative.parts)
        ):
            names.append(relative.as_posix())
    return sorted(names)


def image_type(data: bytes) -> str | None:
    """`image/png` or `image/jpeg` by the file's own first bytes, whatever it is called."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR" and len(data) >= 33:
        width, height = struct.unpack(">II", data[16:24])
        sane = 0 < width <= MAX_IMAGE_SIDE and 0 < height <= MAX_IMAGE_SIDE
        return "image/png" if sane else None
    if data[:3] == b"\xff\xd8\xff" and len(data) >= 100:
        return "image/jpeg"
    return None


def _logfmt(line: str) -> dict[str, str] | None:
    """The key=value pairs of a logfmt line, or None when the line is something else.

    A leading timestamp, and a level after it, are accepted before the pairs.
    """
    values: dict[str, str] = {}
    position = 0
    prefix = ""
    for match in _LOGFMT.finditer(line):
        between = line[position : match.start()]
        if between.strip():
            if values:
                return None
            prefix = between.strip()
        value = match.group(2)
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace('\\"', '"')
        values[match.group(1)] = value
        position = match.end()
    if line[position:].strip() or len(values) < 2:
        return None
    if prefix:
        stamp, _, level = prefix.partition(" ")
        if parse_timestamp(prefix) is not None:
            stamp, level = prefix, ""
        elif parse_timestamp(stamp) is None or " " in level.strip():
            return None
        values = {"ts": stamp, **({"level": level.strip()} if level.strip() else {}), **values}
    return values


def _line_format(lines: list[str]) -> str:
    """ "json", "logfmt" or "text": what nearly every non-empty line of a log is."""
    sample = [line for line in lines if line.strip()][:50]
    if not sample:
        return "text"

    def objects(line: str) -> bool:
        if not line.lstrip().startswith("{"):
            return False
        try:
            return isinstance(json.loads(line), dict)
        except ValueError:
            return False

    for name, test in (("json", objects), ("logfmt", lambda line: _logfmt(line) is not None)):
        if sum(map(test, sample)) >= 0.9 * len(sample):
            return name
    return "text"


def _json_tables(
    value: Any, path: str = "", depth: int = 0
) -> list[tuple[str, list[tuple[str | None, dict]]]]:
    """Every list of records inside a JSON document, named by its path.

    A list of records is a list of objects, or an object that maps IDs to
    records: all of its values are flat objects that share a field.
    """
    if isinstance(value, list):
        rows = [(None, item) for item in value if isinstance(item, dict)]
        return [(path or "item", rows)] if rows and len(rows) * 2 >= len(value) else []
    if not isinstance(value, dict) or depth >= 4:
        return []
    children = list(value.values())
    keyed = (
        len(children) >= 2
        and all(isinstance(child, dict) and child for child in children)
        and bool(set(children[0]) & set(children[1]))
        and not any(
            isinstance(item, list) and item and isinstance(item[0], dict)
            for child in children[:20]
            for item in child.values()
        )
    )
    if keyed:
        return [(path or "item", [(str(key), child) for key, child in value.items()])]
    tables = []
    for key, child in value.items():
        tables += _json_tables(child, f"{path}.{key}" if path else str(key), depth + 1)
    return tables


def _terms(text: str) -> list[str]:
    return re.findall(r"[\w]+", text.casefold(), flags=re.UNICODE)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ToolError("A numeric, non-empty value is required")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ToolError(
            "Numeric values must use a decimal point and no currency/group separators"
        ) from exc
    if not result.is_finite() or abs(result) > Decimal("1e100"):
        raise ToolError("Numeric value exceeds the supported finite range")
    return result


def _decode(data: bytes) -> str:
    """UTF-8 first; exports from office software are often Windows-1252."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _delimiter(header: str) -> str:
    """The separator the header line uses most, outside quoted cells."""
    outside = re.sub(r'"[^"]*"', "", header)
    counts = {candidate: outside.count(candidate) for candidate in ",;\t|"}
    best = max(counts, key=lambda candidate: (counts[candidate], candidate == ","))
    return best if counts[best] else ","


class _HTMLText(HTMLParser):
    """Visible text of an HTML document, one line per block element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {"script", "style"}:
            self._hidden += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._hidden = max(0, self._hidden - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


_ODF_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODF_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODF_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
# Reviewer comments, cell notes and tracked changes are not the document's text.
_ODF_ASIDE = {
    _ODF_OFFICE + "annotation",
    _ODF_OFFICE + "annotation-end",
    _ODF_TEXT + "tracked-changes",
    _ODF_TEXT + "note",
}


def _odf_text(node: ElementTree.Element) -> str:
    """The text of one OpenDocument paragraph or heading, without comments."""
    parts: list[str] = []

    def walk(element: ElementTree.Element, top: bool) -> None:
        if element.tag not in _ODF_ASIDE:
            if element.tag == _ODF_TEXT + "tab":
                parts.append("\t")
            elif element.tag == _ODF_TEXT + "line-break":
                parts.append("\n")
            elif element.tag == _ODF_TEXT + "s":
                count = element.get(_ODF_TEXT + "c") or "1"
                parts.append(" " * (int(count) if count.isdigit() else 1))
            parts.append(element.text or "")
            for child in element:
                walk(child, False)
        if not top:
            parts.append(element.tail or "")

    walk(node, True)
    return "".join(parts)


def _odf_paragraphs(body: ElementTree.Element) -> list[ElementTree.Element]:
    """Paragraphs and headings in document order, outside comments."""
    found: list[ElementTree.Element] = []

    def walk(element: ElementTree.Element) -> None:
        for child in element:
            if child.tag in _ODF_ASIDE:
                continue
            if child.tag in (_ODF_TEXT + "p", _ODF_TEXT + "h"):
                found.append(child)
            else:
                walk(child)

    walk(body)
    return found


def _ods_rows(body: ElementTree.Element) -> Iterator[tuple[str, int, list[str]]]:
    """(sheet, 1-based row as the sheet shows it, cell texts) of every non-empty row."""

    def repeated(element: ElementTree.Element, name: str) -> int:
        value = element.get(_ODF_TABLE + name) or "1"
        return int(value) if value.isdigit() else 1

    for table in body.iter(_ODF_TABLE + "table"):
        sheet = table.get(_ODF_TABLE + "name") or "Sheet"
        number = 0
        for row in table.iter(_ODF_TABLE + "table-row"):
            repeat = repeated(row, "number-rows-repeated")
            cells: list[str] = []
            for cell in row:
                if cell.tag.endswith("table-cell") and len(cells) < MAX_COLUMNS:
                    # Direct paragraphs only: a cell note is an annotation, not content.
                    text = "\n".join(_odf_text(p) for p in cell if p.tag == _ODF_TEXT + "p")
                    span = repeated(cell, "number-columns-repeated")
                    cells.extend([text] * min(span, MAX_COLUMNS - len(cells)))
            while cells and not cells[-1].strip():
                cells.pop()
            if cells:
                # A filled row repeated a million times is padding, not data.
                for extra in range(min(repeat, 50)):
                    yield sheet, number + extra + 1, cells
            number += repeat


def _html_lines(content: str) -> list[str]:
    parser = _HTMLText()
    parser.feed(content)
    parser.close()
    lines = (" ".join(line.split()).strip(" |") for line in "".join(parser.parts).splitlines())
    return [line for line in lines if line]


def _number_parts(value: Any) -> tuple[str, bool, str]:
    """Digits with separators, sign, and what the separators say about the convention."""
    text = _CURRENCY.sub("", str(value).strip())
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = re.sub(r"[\s\u00a0\u202f'’]", "", text)
    if text.endswith("-"):
        negative, text = not negative, text[:-1]
    if text[:1] in {"+", "-"}:
        negative, text = negative != (text[0] == "-"), text[1:]
    if not re.fullmatch(r"[0-9.,]*[0-9][0-9.,]*", text):
        raise ToolError("A numeric, non-empty value is required")
    dots, commas = text.count("."), text.count(",")
    if dots and commas:
        return text, negative, "point" if text.rfind(".") > text.rfind(",") else "comma"
    if not dots and not commas:
        return text, negative, "none"
    separator = "." if dots else ","
    grouped = re.fullmatch(rf"\d{{1,3}}(\{separator}\d{{3}})+", text) is not None
    if dots + commas > 1:
        if not grouped:
            raise ToolError("Number has misplaced separators")
        # A separator that repeats can only group thousands.
        return text, negative, "comma" if separator == "." else "point"
    if grouped:
        return text, negative, "ambiguous"
    return text, negative, "point" if separator == "." else "comma"


def parse_numbers(values: list[Any], style: str = "auto") -> tuple[list[Decimal], str, int]:
    """Read one column of numbers under a single decimal convention.

    Returns the numbers, the convention used ("point" or "comma") and how many
    values would also be valid numbers under the other convention, such as
    1.234. Currency signs, spaces and apostrophes are ignored.
    """
    direct: dict[int, Decimal] = {}
    parts: dict[int, tuple[str, bool, str]] = {}
    for index, value in enumerate(values):
        if isinstance(value, bool) or value is None:
            raise ToolError("A numeric, non-empty value is required")
        if isinstance(value, (int, float)):
            direct[index] = _decimal(value)
        else:
            parts[index] = _number_parts(value)
    evidence = {kind for _, _, kind in parts.values()} & {"point", "comma"}
    if style == "auto":
        if len(evidence) > 1:
            raise ToolError("Column mixes decimal point and decimal comma; set decimal_separator")
        style = evidence.pop() if evidence else "point"
    group, decimal = (",", ".") if style == "point" else (".", ",")
    numbers = []
    for index in range(len(values)):
        if index in direct:
            numbers.append(direct[index])
            continue
        text, negative, _ = parts[index]
        whole, _, fraction = text.partition(decimal)
        if group in fraction or (
            group in whole and not re.fullmatch(rf"\d{{1,3}}(\{group}\d{{3}})+", whole)
        ):
            raise ToolError("Number does not fit the column's decimal convention")
        number = _decimal(whole.replace(group, "") + ("." + fraction if fraction else ""))
        numbers.append(-number if negative else number)
    ambiguous = sum(kind == "ambiguous" for _, _, kind in parts.values())
    return numbers, style, ambiguous


def _number_text(value: Decimal) -> str:
    if not value.is_finite() or abs(value) > Decimal("1e100"):
        raise ToolError("Arithmetic result exceeds the supported finite range")
    return format(value.normalize(), "f") if value else "0"


def calculate(expression: str) -> str:
    """Evaluate only bounded arithmetic nodes, using Decimal rather than eval()."""
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ToolError("Invalid arithmetic expression") from exc
    if len(list(ast.walk(tree))) > 80:
        raise ToolError("Arithmetic expression is too complex")

    def visit(node: ast.AST, depth: int = 0) -> Decimal:
        if depth > 20:
            raise ToolError("Arithmetic expression is too deeply nested")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            # Parse the source segment so decimal literals never round through float.
            source = ast.get_source_segment(expression, node)
            if source is None or not re.fullmatch(r"[0-9_.eE+\-]+", source):
                raise ToolError("Only decimal numeric literals are supported")
            return _decimal(source.replace("_", ""))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = visit(node.operand, depth + 1)
            return -value if isinstance(node.op, ast.USub) else value
        if not isinstance(node, ast.BinOp):
            raise ToolError("Only numbers and + - * / // % ** arithmetic are allowed")
        left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        elif isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            if right == 0:
                raise ToolError("Division by zero")
            if isinstance(node.op, ast.Div):
                result = left / right
            elif isinstance(node.op, ast.FloorDiv):
                result = (left / right).to_integral_value(rounding=ROUND_FLOOR)
            else:
                result = left - right * (left / right).to_integral_value(rounding=ROUND_FLOOR)
        elif isinstance(node.op, ast.Pow):
            if right != right.to_integral_value() or abs(right) > 12:
                raise ToolError("Exponent must be an integer between -12 and 12")
            if left == 0 and right < 0:
                raise ToolError("Division by zero")
            result = left ** int(right)
        else:
            raise ToolError("Unsupported arithmetic operator")
        _number_text(result)
        return result

    with localcontext() as context:
        context.prec = 50
        try:
            return _number_text(visit(tree.body))
        except (ArithmeticError, ValueError) as exc:
            if isinstance(exc, ToolError):
                raise
            raise ToolError("Arithmetic expression has an invalid or out-of-range result") from exc


class ToolRegistry:
    """Read-only local tools scoped to a root and, optionally, a task document list."""

    _arguments = {
        "search": SearchArgs,
        "read": ReadArgs,
        "calculate": CalculateArgs,
        "table": TableArgs,
    }

    def __init__(
        self,
        root: Path,
        allowed_documents: list[str] | None = None,
        decimal_separator: str = "auto",
        images: bool = False,
    ):
        self.decimal_separator = decimal_separator
        # Whether pictures among the task's files are listed as images and may
        # be handed to the model. Off, a picture is an unreadable document.
        self.images = images
        self.allowed = None if allowed_documents is None else frozenset(allowed_documents)
        if self.allowed is not None and len(self.allowed) > MAX_DOCUMENTS:
            raise ToolError(f"At most {MAX_DOCUMENTS} task documents are supported")
        if self.allowed == frozenset() and not root.is_dir():
            # A task without documents works through other tools: nothing is
            # ever opened below the root, so it need not exist.
            root = Path.cwd()
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ToolError("Document root must be a directory")
        self._cache: dict[str, tuple[tuple[int, ...], Document]] = {}
        self._index: tuple[tuple[str, ...], Any] | None = None

    def rescope(self, documents: Iterable[str]) -> None:
        """The task's documents after its files changed.

        New ones become readable and removed ones stop being offered. A file
        with new content is parsed again by itself: `_load` compares what is
        on disk with what it parsed. The search index is built again.
        """
        allowed = frozenset(documents)
        if len(allowed) > MAX_DOCUMENTS:
            raise ToolError(f"At most {MAX_DOCUMENTS} task documents are supported")
        if self.allowed is not None:
            self.allowed = allowed
        for name in set(self._cache) - allowed:
            del self._cache[name]
        self._index = None

    def inventory(self) -> list[dict[str, Any]]:
        """Size and columns of every task document, so a table needs no discovery turn."""
        listing = []
        for name in self._names():
            try:
                loaded = self._load(name)
                entry: dict[str, Any] = {"document": name, "units": len(loaded.units)}
                if loaded.columns:
                    entry["columns"] = loaded.columns[:40]
            except (ToolError, OSError, ValueError, UnicodeError, csv.Error, zipfile.BadZipFile):
                entry = {"document": name, "error": "unreadable"}
            listing.append(entry)
        for name in self._image_names():
            try:
                listing.append({"document": name, "image": self._path(name).stat().st_size})
            except (ToolError, OSError):
                listing.append({"document": name, "error": "unreadable"})
        return listing

    def _image_names(self) -> list[str]:
        if not self.images:
            return []
        names = sorted(self.allowed) if self.allowed is not None else list_images(self.root)
        return [name for name in names if Path(name).suffix.lower() in IMAGE_TYPES]

    def image(self, name: str) -> tuple[bytes, str, str]:
        """A picture of this task: its bytes, its media type and its SHA-256.

        Only a file of the task, only PNG or JPEG by its own first bytes, and
        only up to a size that one request can carry.
        """
        if not self.images:
            raise ToolError("Images are not read in this configuration")
        path = self._path(name)
        if path.suffix.lower() not in IMAGE_TYPES:
            raise ToolError("Not an image; use read, search or table for documents")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ToolError(f"Image exceeds the {MAX_IMAGE_BYTES // 1_000_000} MB limit")
        data = path.read_bytes()
        kind = image_type(data)
        if kind is None:
            raise ToolError("Not a readable PNG or JPEG image")
        return data, kind, hashlib.sha256(data).hexdigest()

    def _time(self, loaded: Document) -> str | None:
        if not loaded.timed:
            rows = [unit.values for unit in loaded.units if unit.values is not None]
            loaded.time = time_field(loaded.columns, rows)
            loaded.timed = True
        return loaded.time

    def time_column(self, document: str) -> str | None:
        """The column that carries the time of a row: a usual name, else by its values."""
        return self._time(self._load(document))

    def _ordered(self, loaded: Document) -> list[Unit]:
        """Table rows in time order when the table has a time column, else in file order."""
        if loaded.ordered is None:
            rows = [unit for unit in loaded.units if unit.values is not None]
            column = self._time(loaded)
            if column is not None:
                moments = [parse_timestamp(unit.values.get(column)) for unit in rows]
                if all(moment is not None for moment in moments):
                    order = sorted(range(len(rows)), key=lambda index: (moments[index], index))
                    rows = [rows[index] for index in order]
            loaded.ordered = rows
        return loaded.ordered

    def profile(self, document: str) -> dict[str, dict[str, Any]]:
        """Per column of a table: how many distinct values, the frequent ones, a time range.

        This is the map a reader needs before filtering: which actions exist,
        how many actors there are, what period the log covers.
        """
        loaded = self._load(document)
        if loaded.profile is None:
            profile: dict[str, dict[str, Any]] = {}
            moment_column = self._time(loaded)
            for column in loaded.columns[:MAX_COLUMNS]:
                counts = Counter(
                    "" if unit.values.get(column) is None else str(unit.values[column])
                    for unit in self._ordered(loaded)
                )
                entry: dict[str, Any] = {"distinct": len(counts)}
                if column == moment_column and counts:
                    ordered = self._ordered(loaded)
                    entry["range"] = (
                        str(ordered[0].values.get(column)),
                        str(ordered[-1].values.get(column)),
                    )
                elif len(counts) <= 40:
                    entry["values"] = dict(
                        sorted(counts.items(), key=lambda item: (-item[1], item[0]))
                    )
                profile[column] = entry
            loaded.profile = profile
        return loaded.profile

    def rows(self, document: str, where: dict[str, Any] | None = None) -> list[Unit]:
        """Structured units of a table or log that satisfy `where`, in time order."""
        loaded = self._load(document)
        if not loaded.columns:
            raise ToolError("Table requires a CSV, JSONL, JSON-array or XLSX document")
        try:
            where = normalize_where(where, loaded.columns)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if len(where) > 20:
            raise ToolError("Filter has more than 20 conditions")
        unknown = sorted({where_column(key) for key in where} - set(loaded.columns))
        if unknown:
            raise ToolError(
                f"Unknown column {unknown[0][:60]!r}; columns: {', '.join(loaded.columns[:40])}"
            )
        return [unit for unit in self._ordered(loaded) if matches(unit.values, where)]

    def after(
        self,
        document: str,
        key: str,
        first: dict[str, Any],
        where: dict[str, Any] | None = None,
        time: str | None = None,
    ) -> tuple[list[Unit], int]:
        """Rows matching `where` of each `key` value, from its first row matching `first` on.

        "What did every compromised agent do after its compromise": `first`
        selects the trigger rows, and the earliest one per key value starts
        that value's clock. Returns the rows in time order and how many key
        values had a trigger row.
        """
        columns = self.columns(document)
        time = time or self.time_column(document)
        if key not in columns:
            raise ToolError(f"key must be a column: {', '.join(columns[:40])}")
        if not time or time not in columns:
            raise ToolError("no time column found; pass time")
        if not first:
            raise ToolError("first must be a filter for the trigger rows")
        started: dict[str, datetime] = {}
        for unit in self.rows(document, first):
            moment = parse_timestamp(unit.values.get(time))
            who = unit.values.get(key)
            if moment is None or who in (None, ""):
                continue
            who = str(who)
            started[who] = min(started[who], moment) if who in started else moment
        selected = []
        for unit in self.rows(document, where):
            moment = parse_timestamp(unit.values.get(time))
            begin = started.get(str(unit.values.get(key)))
            if moment is not None and begin is not None and moment >= begin:
                selected.append(unit)
        return selected, len(started)

    def units(self, document: str) -> list[Unit]:
        return self._load(document).units

    def columns(self, document: str) -> list[str]:
        return self._load(document).columns

    def argument_names(self, name: str) -> set[str]:
        return set(self._arguments[name].model_fields) if name in self._arguments else set()

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name not in self._arguments:
                raise ToolError("Unknown tool; available tools: search, read, calculate, table")
            parsed = self._arguments[name].model_validate(arguments)
            return getattr(self, "_" + name)(**parsed.model_dump())
        except ValidationError as exc:
            # Field names only: values may be document text or secrets.
            wrong = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
            allowed = ", ".join(self._arguments[name].model_fields)
            return {
                "error": f"Invalid arguments ({', '.join(wrong) or 'shape'}); "
                f"{name} takes: {allowed}"
            }
        except ToolError as exc:
            return {"error": str(exc)}
        except (
            OSError,
            UnicodeError,
            csv.Error,
            zipfile.BadZipFile,
            ValueError,
            TypeError,
            RecursionError,
        ):
            return {"error": "The selected document could not be parsed safely"}

    def _path(self, name: str) -> Path:
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts or "\x00" in name:
            raise ToolError("Document must be a relative path within the document root")
        if self.allowed is not None and name not in self.allowed:
            raise ToolError("Document is outside this task's allowed document list")
        try:
            resolved = (self.root / candidate).resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, ValueError, RuntimeError) as exc:
            raise ToolError("Document is missing or outside the document root") from exc
        if not resolved.is_file():
            raise ToolError("Document must be a regular file")
        if resolved.stat().st_size > MAX_FILE_BYTES:
            raise ToolError(f"Document exceeds the {MAX_FILE_BYTES // 2**20} MiB input limit")
        return resolved

    def _names(self) -> list[str]:
        if self.allowed is not None:
            names = sorted(self.allowed)
            if self.images:
                # Listed apart: a picture is not parsed and not searched.
                names = [name for name in names if Path(name).suffix.lower() not in IMAGE_TYPES]
            return names
        # An explicit task allowlist is preferred. Unscoped usage remains bounded.
        return list_documents(self.root)

    @staticmethod
    def _archive_guard(path: Path) -> None:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000:
                raise ToolError("Document archive has too many entries")
            total = 0
            for info in entries:
                total += info.file_size
                if info.file_size > MAX_ARCHIVE_BYTES or total > MAX_ARCHIVE_BYTES:
                    raise ToolError("Document archive exceeds the 32 MiB expansion limit")
                if (
                    info.file_size > 1024 * 1024
                    and info.file_size > max(1, info.compress_size) * 150
                ):
                    raise ToolError("Document archive has an unsafe compression ratio")

    def _load(self, name: str) -> Document:
        path = self._path(name)
        # Unchanged size, modification time, change time and inode: the bytes
        # are the ones already hashed. A writer can restore the modification
        # time, not the change time, so stale evidence is still caught.
        status = path.stat()
        stamp = (status.st_size, status.st_mtime_ns, status.st_ctime_ns, status.st_ino)
        known = self._cache.get(name)
        if known is not None and known[0] == stamp:
            return known[1]
        data = path.read_bytes()
        signature = hashlib.sha256(data).hexdigest()
        shared = (str(path), signature, name)
        document = known[1] if known is not None and known[1].digest == signature else None
        document = document or _shared(shared)
        if document is None:
            document = _shared(shared, self._parse(name, path, data, signature))
        self._cache[name] = (stamp, document)
        return document

    def _parse(self, name: str, path: Path, data: bytes, signature: str) -> Document:
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED and not _looks_like_text(data):
            raise ToolError("Unsupported document format")
        units: list[Unit] = []
        columns: list[str] = []
        headers_by_sheet: dict[str, list[str]] = {}
        extracted_chars = 0

        def append(unit: Unit) -> None:
            nonlocal extracted_chars
            extracted_chars += len(unit.text)
            if len(units) >= MAX_UNITS or extracted_chars > MAX_DOCUMENT_CHARS:
                raise ToolError(
                    f"Document exceeds the {MAX_UNITS}-unit / "
                    f"{MAX_DOCUMENT_CHARS}-character extraction limit"
                )
            units.append(unit)

        def record(locator: str, text: str, item: Any) -> None:
            """A log entry: its fields become filterable columns when it is an object."""
            if not isinstance(item, dict):
                append(Unit(locator, text))
                return
            values = flatten(item, limit=MAX_COLUMNS)
            for key in values:
                if key and key not in columns and len(columns) < MAX_COLUMNS:
                    columns.append(key)
            append(Unit(locator, text, values))

        if suffix in TEXT_SUFFIXES or suffix in {".jsonl", ".ndjson"} or suffix not in SUPPORTED:
            lines = _decode(data).splitlines()
            kind = "json" if suffix in {".jsonl", ".ndjson"} else "text"
            if suffix in SNIFFED_SUFFIXES or suffix not in SUPPORTED:
                kind = _line_format(lines)
            for i, line in enumerate(lines, 1):
                if kind == "text":
                    append(Unit(f"line:{i}", line))
                elif line.strip():
                    try:
                        item = json.loads(line) if kind == "json" else _logfmt(line)
                    except ValueError:
                        item = None  # A damaged line stays readable as text.
                    record(f"line:{i}", line, item)
        elif suffix == ".json":
            content = _decode(data)
            # Reject malformed JSON instead of silently treating it as text.
            tables = _json_tables(json.loads(content))
            for table, rows in tables:
                for i, (key, item) in enumerate(rows, 1):
                    # Several lists of records in one file stay apart by `_table`;
                    # a record that was stored under an ID carries it as `_key`.
                    if len(tables) > 1:
                        item = {"_table": table, **item}
                    if key is not None:
                        item = {"_key": key, **item}
                    record(f"{table}:{i}", self._row_text(item), item)
            if not tables:
                for i, line in enumerate(content.splitlines(), 1):
                    append(Unit(f"line:{i}", line))
        elif suffix in {".html", ".htm"}:
            for i, line in enumerate(_html_lines(_decode(data)), 1):
                append(Unit(f"line:{i}", line))
        elif suffix == ".eml":
            message = message_from_bytes(data, policy=policy.default)
            lines = [
                f"{header}: {message[header]}"
                for header in ("From", "To", "Cc", "Date", "Subject")
                if message[header]
            ]
            body = message.get_body(preferencelist=("plain", "html"))
            if body is not None:
                text = body.get_content()
                lines += (
                    _html_lines(text) if body.get_content_subtype() == "html" else text.splitlines()
                )
            for i, line in enumerate(lines, 1):
                append(Unit(f"line:{i}", line))
        elif suffix in {".csv", ".tsv"}:
            content = _decode(data)
            delimiter = "\t" if suffix == ".tsv" else _delimiter(content.split("\n", 1)[0])
            reader = csv.reader(io.StringIO(content, newline=""), delimiter=delimiter)
            columns = self._headers(next((cells for cells in reader if cells), []))
            width = len(columns)
            for row_number, cells in enumerate((cells for cells in reader if cells), 2):
                # One ragged row must not hide ten thousand good ones: missing
                # cells are empty, surplus cells stay in the last column.
                if len(cells) > width:
                    cells = cells[: width - 1] + [delimiter.join(cells[width - 1 :])]
                values = dict(zip(columns, cells + [""] * (width - len(cells)), strict=True))
                append(Unit(f"row:{row_number}", self._row_text(values), values))
        elif suffix == ".pdf":
            from pypdf import PdfReader

            try:
                reader = PdfReader(path, strict=True)
                if reader.is_encrypted:
                    raise ToolError("Encrypted PDFs are not supported")
                if len(reader.pages) > 200:
                    raise ToolError("PDF exceeds the 200-page limit")
                for i, page in enumerate(reader.pages, 1):
                    append(Unit(f"page:{i}", page.extract_text() or ""))
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(
                    "PDF could not be parsed; scanned PDFs require offline OCR first"
                ) from exc
            if not any(unit.text.strip() for unit in units):
                raise ToolError("PDF has no extractable text; run offline OCR before ingestion")
        elif suffix == ".docx":
            from docx import Document as DocxDocument

            self._archive_guard(path)
            try:
                doc = DocxDocument(path)
                # Preserve body order, including tables, so locators do not depend on tool calls.
                for block in doc.iter_inner_content():
                    if hasattr(block, "rows"):
                        for row in block.rows:
                            append(
                                Unit(
                                    f"line:{len(units) + 1}", " | ".join(c.text for c in row.cells)
                                )
                            )
                    else:
                        append(Unit(f"line:{len(units) + 1}", block.text))
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError("DOCX could not be parsed") from exc
        elif suffix in {".odt", ".ods"}:
            self._archive_guard(path)
            try:
                with zipfile.ZipFile(path) as archive:
                    body = ElementTree.fromstring(archive.read("content.xml"))
                if suffix == ".odt":
                    # Non-empty paragraphs and headings in document order, numbered
                    # from 1: the locator the event asks for. Comments are left out.
                    texts = [_odf_text(node) for node in _odf_paragraphs(body)]
                    for i, text in enumerate((text for text in texts if text.strip()), 1):
                        append(Unit(f"paragraph:{i}", text))
                else:
                    for sheet, number, cells in _ods_rows(body):
                        if sheet not in headers_by_sheet:
                            headers_by_sheet[sheet] = self._headers(cells)
                            columns.extend(c for c in headers_by_sheet[sheet] if c not in columns)
                            continue
                        names = headers_by_sheet[sheet]
                        values = dict.fromkeys(names)
                        values.update(zip(names, cells, strict=False))
                        append(Unit(f"sheet:{sheet}:row:{number}", self._row_text(values), values))
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError("OpenDocument file could not be parsed") from exc
        elif suffix == ".xlsx":
            from openpyxl import load_workbook

            self._archive_guard(path)
            workbook = None
            try:
                # data_only does not calculate formulas. Reject formula cells explicitly below.
                workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
                if len(workbook.worksheets) > 20:
                    raise ToolError("Workbook exceeds the 20-sheet limit")
                for sheet in workbook.worksheets:
                    if sheet.max_column and sheet.max_column > 100:
                        raise ToolError("Workbook exceeds the 100-column limit")
                    if sheet.max_row and sheet.max_row > MAX_UNITS + 1:
                        raise ToolError(f"Workbook exceeds the {MAX_UNITS}-row limit")
                    rows = sheet.iter_rows()
                    header_cells = next(rows, ())
                    if not header_cells:
                        continue  # An empty sheet beside the data, as most workbooks have.
                    if any(c.data_type == "f" for c in header_cells):
                        raise ToolError("Workbook formula headers are not supported")
                    headers = self._headers(
                        [str(c.value) if c.value is not None else "" for c in header_cells]
                    )
                    columns.extend(c for c in headers if c not in columns)
                    for row_number, row in enumerate(rows, 2):
                        if any(c.data_type == "f" for c in row):
                            raise ToolError(
                                "Workbook formulas are not evaluated; export values-only CSV first"
                            )
                        # A short row has empty cells; cells beyond the header have no name.
                        values = dict.fromkeys(headers)
                        values.update(
                            (header, self._cell(c.value))
                            for header, c in zip(headers, row, strict=False)
                        )
                        append(
                            Unit(
                                f"sheet:{sheet.title}:row:{row_number}",
                                self._row_text(values),
                                values,
                            )
                        )
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError("XLSX could not be parsed") from exc
            finally:
                if workbook is not None:
                    workbook.close()
        return Document(name, units, signature, columns)

    @staticmethod
    def _cell(value: Any) -> str | int | float | bool | None:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    @staticmethod
    def _row_text(values: dict[str, Any]) -> str:
        return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _headers(cells: list[str]) -> list[str]:
        """Usable column names from a header row: none empty, none twice.

        An export with an unnamed index column or a repeated header is still a
        table; the names are completed instead of refusing the document.
        """
        if not cells or len(cells) > MAX_COLUMNS:
            raise ToolError(f"Table requires 1–{MAX_COLUMNS} header cells")
        names: list[str] = []
        for position, cell in enumerate(cells, 1):
            name = str(cell).strip()[:200] or f"column_{position}"
            base, repeat = name, 2
            while name in names:
                name, repeat = f"{base}_{repeat}", repeat + 1
            names.append(name)
        return names

    @staticmethod
    def _snippet(text: str, query_terms: set[str] | None = None) -> str:
        if len(text) <= MAX_QUOTE_CHARS:
            return text
        offset = 0
        if query_terms:
            matches = [
                m.start() for m in re.finditer(r"\w+", text) if m.group().casefold() in query_terms
            ]
            if matches:
                offset = max(0, matches[0] - MAX_QUOTE_CHARS // 4)
        return text[offset : offset + MAX_QUOTE_CHARS]

    @staticmethod
    def _bounded_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        selected, size = [], 0
        for row in rows:
            row_size = len(json.dumps(row, ensure_ascii=False))
            if size + row_size > MAX_RESPONSE_CHARS:
                return selected, True
            selected.append(row)
            size += row_size
        return selected, False

    def _read(self, document: str, start: int, limit: int) -> dict[str, Any]:
        loaded = self._load(document)
        units = loaded.units[start - 1 : start - 1 + limit]
        rows = [
            {"document": document, "locator": unit.locator, "quote": self._snippet(unit.text)}
            for unit in units
        ]
        rows, bounded = self._bounded_rows(rows)
        return {
            "results": rows,
            "total_units": len(loaded.units),
            "next_start": start + len(rows) if start - 1 + len(rows) < len(loaded.units) else None,
            "sha256": loaded.digest,
            "truncated": bounded
            or start - 1 + len(rows) < len(loaded.units)
            or any(len(unit.text) > MAX_QUOTE_CHARS for unit in units[: len(rows)]),
        }

    def _scan(self) -> dict[str, Any]:
        """Tokenise the task documents once; later searches reuse the index."""
        names = self._names()
        loaded: list[tuple[str, Document]] = []
        errors: list[dict[str, str]] = []
        for name in names:
            try:
                loaded.append((name, self._load(name)))
            except (
                ToolError,
                OSError,
                ValueError,
                UnicodeError,
                csv.Error,
                zipfile.BadZipFile,
                RecursionError,
            ) as exc:
                # A single bad document must not erase valid evidence in the remaining scope.
                errors.append({"document": name, "error": str(exc)[:200]})
        signature = tuple(f"{name}:{document.digest}" for name, document in loaded)
        if self._index is not None and self._index[0] == signature:
            return self._index[1]
        records: list[tuple[str, Unit, int]] = []
        postings: dict[str, dict[int, int]] = {}
        scanned_chars = 0
        unscanned = 0
        for position, (name, document) in enumerate(loaded):
            scanned_chars += sum(len(unit.text) for unit in document.units)
            if (
                scanned_chars > MAX_SEARCH_CHARS
                or len(records) + len(document.units) > MAX_SEARCH_UNITS
            ):
                unscanned = len(loaded) - position
                errors.append(
                    {
                        "document": name,
                        "error": "Search scan limit reached; read directly or narrow the task",
                    }
                )
                break
            for unit in document.units:
                terms = _terms(unit.text)
                for term, count in Counter(terms).items():
                    postings.setdefault(term, {})[len(records)] = count
                records.append((name, unit, len(terms)))
        index = {
            "records": records,
            "postings": postings,
            "errors": errors,
            "unscanned": unscanned,
            "average": sum(length for _, _, length in records) / max(len(records), 1),
        }
        self._index = (signature, index)
        return index

    def _search(self, query: str, limit: int) -> dict[str, Any]:
        wanted = set(_terms(query))
        if not wanted:
            raise ToolError("Search requires at least one letter or number")
        index = self._scan()
        records, errors = index["records"], index["errors"]
        scores: dict[int, float] = {}
        for term in sorted(wanted):
            posting = index["postings"].get(term, {})
            if not posting:
                continue
            idf = math.log(1 + (len(records) - len(posting) + 0.5) / (len(posting) + 0.5))
            for position, count in posting.items():
                length = records[position][2]
                scores[position] = scores.get(position, 0.0) + (
                    idf
                    * count
                    * 2.2
                    / (count + 1.2 * (0.25 + 0.75 * length / max(index["average"], 1)))
                )
        ranked = sorted(
            (
                (score, records[position][0], records[position][1])
                for position, score in scores.items()
            ),
            key=lambda item: (-item[0], item[1], item[2].locator),
        )
        rows = [
            {
                "document": name,
                "locator": unit.locator,
                "quote": self._snippet(unit.text, wanted),
                "score": round(score, 5),
            }
            for score, name, unit in ranked[:limit]
        ]
        rows, bounded = self._bounded_rows(rows)
        return {
            "results": rows,
            "matched_units": len(ranked),
            "scanned_units": len(records),
            "unscanned_documents": index["unscanned"],
            "errors": errors[:20],
            "truncated": bounded
            or len(ranked) > len(rows)
            or len(errors) > 20
            or index["unscanned"] > 0,
            "coverage": "partial" if errors else "complete",
        }

    def _calculate(self, expression: str) -> dict[str, Any]:
        return {"value": calculate(expression), "precision_digits": 50}

    def _table(
        self, document: str, operation: str, column: str | None, where: dict | None, limit: int
    ) -> dict[str, Any]:
        loaded = self._load(document)
        matching = self.rows(document, where)
        if column is not None and column not in loaded.columns:
            raise ToolError(f"Unknown column; columns: {', '.join(loaded.columns[:40])}")

        def cite(unit: Unit) -> dict[str, Any]:
            return {
                "document": document,
                "locator": unit.locator,
                "quote": self._snippet(unit.text),
            }

        rows = [{**cite(unit), "values": unit.values} for unit in matching[:limit]]
        rows, bounded = self._bounded_rows(rows)
        if operation == "rows":
            return {
                "rows": rows,
                "columns": loaded.columns,
                "matched_rows": len(matching),
                "truncated": bounded or len(matching) > len(rows),
            }
        if operation in {"group", "distinct"}:
            if column is None:
                raise ToolError("A column is required for group and distinct")
            first: dict[str, Unit] = {}
            for unit in matching:
                value = unit.values.get(column)
                first.setdefault("" if value is None else str(value), unit)
            counts = count_by((unit.values for unit in matching), column)
            return {
                "groups": [
                    {"value": value, "count": count, **cite(first[value])}
                    for value, count in counts[:limit]
                ],
                "operation": operation,
                "column": column,
                "distinct_values": len(counts),
                "matched_rows": len(matching),
                "truncated": len(counts) > limit,
            }
        if operation == "count":
            value = str(len(matching))
        else:
            if column is None:
                raise ToolError("A column is required for numeric aggregation")
            if not matching:
                raise ToolError("No matching rows; a numeric aggregate is undefined")
            with localcontext() as context:
                context.prec = 50
                numbers, number_format, ambiguous = parse_numbers(
                    [unit.values.get(column) for unit in matching], self.decimal_separator
                )
                if operation == "sum":
                    numeric = sum(numbers, Decimal(0))
                elif operation == "mean":
                    numeric = sum(numbers, Decimal(0)) / len(numbers)
                elif operation == "min":
                    numeric = min(numbers)
                else:
                    numeric = max(numbers)
                value = _number_text(numeric)
        interpretation: dict[str, Any] = {}
        if operation != "count":
            interpretation["number_format"] = "decimal_" + number_format
            if ambiguous:
                # Values such as 1.234 are valid under both conventions.
                interpretation["ambiguous_numbers"] = ambiguous
        return {
            "value": value,
            **interpretation,
            "operation": operation,
            "column": column,
            "matched_rows": len(matching),
            "citations": [
                {key: row[key] for key in ("document", "locator", "quote")} for row in rows
            ],
            "sha256": loaded.digest,
            "truncated": bounded or len(matching) > len(rows),
        }

    def verify_citations(self, citations: list[Citation]) -> list[str]:
        """Check citation existence and exact text; this does not prove semantic entailment."""
        errors = []
        for index, citation in enumerate(citations):
            try:
                if self.images and Path(citation.document).suffix.lower() in IMAGE_TYPES:
                    # What the model read in a picture cannot be compared with
                    # its bytes; that it is still the same picture can.
                    if citation.locator != f"image:{self.image(citation.document)[2][:16]}":
                        raise ToolError("The image is not the one that was read")
                    continue
                loaded = self._load(citation.document)
                unit = next((u for u in loaded.units if u.locator == citation.locator), None)
                if unit is None:
                    raise ToolError("Locator does not exist in this document")
                if not citation.quote or citation.quote not in unit.text:
                    raise ToolError("Quote is absent from the specified document locator")
            except (ToolError, OSError, ValueError, UnicodeError) as exc:
                errors.append(f"citation[{index}]: {str(exc)[:200]}")
        return errors
