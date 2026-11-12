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
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal, InvalidOperation, localcontext
from email import message_from_bytes, policy
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from hacklab.models import Citation, StrictModel

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_DOCUMENT_CHARS = 500_000
MAX_UNITS = 10_000
MAX_SEARCH_CHARS = 2_000_000
MAX_SEARCH_UNITS = 20_000
MAX_RESPONSE_CHARS = 24_000
MAX_QUOTE_CHARS = 2000
SUPPORTED = {
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
    ".eml",
    ".pdf",
    ".docx",
    ".xlsx",
}
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
    limit: int = Field(default=80, ge=1, le=100, strict=True)


class CalculateArgs(StrictModel):
    expression: str = Field(min_length=1, max_length=500)


class TableArgs(StrictModel):
    document: str = Field(min_length=1, max_length=1000)
    operation: Literal["rows", "count", "sum", "min", "max", "mean"] = "rows"
    column: str | None = Field(default=None, max_length=200)
    where: dict[str, str | int | float | bool | None] | None = None
    limit: int = Field(default=30, ge=1, le=100, strict=True)


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
    ):
        self.decimal_separator = decimal_separator
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ToolError("Document root must be a directory")
        self.allowed = None if allowed_documents is None else frozenset(allowed_documents)
        if self.allowed is not None and len(self.allowed) > 200:
            raise ToolError("At most 200 task documents are supported")
        self._cache: dict[str, tuple[str, Document]] = {}

    def schemas(self) -> list[dict[str, Any]]:
        descriptions = {
            "search": "Rank matching evidence snippets in task documents. No web access.",
            "read": "Read document units from a 1-based start (line/page/row).",
            "calculate": "Compute bounded decimal arithmetic without executing code.",
            "table": (
                "Filter exact column values and aggregate CSV/TSV/XLSX rows deterministically. "
                "Aggregates read amounts such as 1.234,56 EUR and report number_format."
            ),
        }
        return [
            {
                "name": name,
                "description": descriptions[name],
                "parameters": args.model_json_schema(),
            }
            for name, args in self._arguments.items()
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name not in self._arguments:
                raise ToolError("Unknown tool; available tools: search, read, calculate, table")
            parsed = self._arguments[name].model_validate(arguments)
            return getattr(self, "_" + name)(**parsed.model_dump())
        except ValidationError:
            return {"error": "Invalid arguments; use the tool's declared argument schema"}
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
        if resolved.suffix.lower() not in SUPPORTED:
            raise ToolError("Unsupported document format")
        if resolved.stat().st_size > MAX_FILE_BYTES:
            raise ToolError("Document exceeds the 8 MiB input limit")
        return resolved

    def _names(self) -> list[str]:
        if self.allowed is not None:
            return sorted(self.allowed)
        # An explicit task allowlist is preferred. Unscoped usage remains bounded.
        names = []
        visited = 0
        for path in self.root.rglob("*"):
            visited += 1
            if visited > 5000:
                raise ToolError("Directory inventory exceeds 5000 entries; supply a document list")
            if path.is_file() and path.suffix.lower() in SUPPORTED:
                names.append(path.relative_to(self.root).as_posix())
                if len(names) > 200:
                    raise ToolError("Directory contains over 200 documents; supply a document list")
        return sorted(names)

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
        # Hash actual bytes on each access: preserved timestamps must not validate stale evidence.
        signature = hashlib.sha256(path.read_bytes()).hexdigest()
        if name in self._cache and self._cache[name][0] == signature:
            return self._cache[name][1]
        suffix = path.suffix.lower()
        units: list[Unit] = []
        columns: list[str] = []
        extracted_chars = 0

        def append(unit: Unit) -> None:
            nonlocal extracted_chars
            extracted_chars += len(unit.text)
            if len(units) >= MAX_UNITS or extracted_chars > MAX_DOCUMENT_CHARS:
                raise ToolError(
                    "Document exceeds the 10000-unit / 500000-character extraction limit"
                )
            units.append(unit)

        if suffix in {".txt", ".md", ".json"}:
            content = _decode(path.read_bytes())
            if suffix == ".json":
                # Reject malformed JSON instead of silently treating it as text.
                json.loads(content)
            for i, line in enumerate(content.splitlines(), 1):
                append(Unit(f"line:{i}", line))
        elif suffix in {".html", ".htm"}:
            for i, line in enumerate(_html_lines(_decode(path.read_bytes())), 1):
                append(Unit(f"line:{i}", line))
        elif suffix == ".eml":
            message = message_from_bytes(path.read_bytes(), policy=policy.default)
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
            content = _decode(path.read_bytes())
            delimiter = "\t" if suffix == ".tsv" else _delimiter(content.split("\n", 1)[0])
            reader = csv.DictReader(io.StringIO(content, newline=""), delimiter=delimiter)
            columns = list(reader.fieldnames or [])
            self._check_columns(columns)
            for row_number, values in enumerate(reader, 2):
                if None in values or any(v is None for v in values.values()):
                    raise ToolError("CSV row has a different number of cells from the header")
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
                        raise ToolError("Workbook exceeds the 10000-row limit")
                    rows = sheet.iter_rows()
                    header_cells = next(rows, ())
                    if any(c.data_type == "f" for c in header_cells):
                        raise ToolError("Workbook formula headers are not supported")
                    headers = [str(c.value) if c.value is not None else "" for c in header_cells]
                    self._check_columns(headers)
                    columns.extend(c for c in headers if c not in columns)
                    for row_number, row in enumerate(rows, 2):
                        if any(c.data_type == "f" for c in row):
                            raise ToolError(
                                "Workbook formulas are not evaluated; export values-only CSV first"
                            )
                        values = {
                            header: self._cell(c.value)
                            for header, c in zip(headers, row, strict=True)
                        }
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
        document = Document(name, units, signature, columns)
        self._cache[name] = (signature, document)
        return document

    @staticmethod
    def _cell(value: Any) -> str | int | float | bool | None:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    @staticmethod
    def _row_text(values: dict[str, Any]) -> str:
        return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _check_columns(columns: list[str]) -> None:
        if not columns or len(columns) > 100 or any(not c or len(c) > 200 for c in columns):
            raise ToolError("Table requires 1–100 non-empty headers of at most 200 characters")
        if len(set(columns)) != len(columns):
            raise ToolError("Table headers must be unique")

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

    def _search(self, query: str, limit: int) -> dict[str, Any]:
        wanted = set(_terms(query))
        if not wanted:
            raise ToolError("Search requires at least one letter or number")
        records: list[tuple[str, Unit, list[str]]] = []
        errors: list[dict[str, str]] = []
        names = self._names()
        scanned_chars = 0
        unscanned_documents = 0
        for index, name in enumerate(names):
            try:
                document = self._load(name)
                scanned_chars += sum(len(unit.text) for unit in document.units)
                if (
                    scanned_chars > MAX_SEARCH_CHARS
                    or len(records) + len(document.units) > MAX_SEARCH_UNITS
                ):
                    unscanned_documents = len(names) - index
                    errors.append(
                        {
                            "document": name,
                            "error": (
                                "Search scan limit reached; read directly or narrow task documents"
                            ),
                        }
                    )
                    break
                records.extend((name, unit, _terms(unit.text)) for unit in document.units)
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
        average_length = sum(len(terms) for _, _, terms in records) / max(len(records), 1)
        frequencies = Counter(term for _, _, terms in records for term in set(terms) & wanted)
        ranked: list[tuple[float, str, Unit]] = []
        for name, unit, terms in records:
            counts = Counter(terms)
            score = 0.0
            for term in wanted:
                count = counts[term]
                if count:
                    idf = math.log(
                        1 + (len(records) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
                    )
                    score += (
                        idf
                        * count
                        * 2.2
                        / (count + 1.2 * (0.25 + 0.75 * len(terms) / max(average_length, 1)))
                    )
            if score:
                ranked.append((score, name, unit))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2].locator))
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
            "unscanned_documents": unscanned_documents,
            "errors": errors[:20],
            "truncated": bounded
            or len(ranked) > len(rows)
            or len(errors) > 20
            or unscanned_documents > 0,
            "coverage": "partial" if errors else "complete",
        }

    def _calculate(self, expression: str) -> dict[str, Any]:
        return {"value": calculate(expression), "precision_digits": 50}

    def _table(
        self, document: str, operation: str, column: str | None, where: dict | None, limit: int
    ) -> dict[str, Any]:
        loaded = self._load(document)
        if not loaded.columns:
            raise ToolError("Table requires a CSV or XLSX document")
        where = where or {}
        if len(where) > 20 or any(key not in loaded.columns for key in where):
            raise ToolError("Filter contains an unknown column or more than 20 conditions")
        if column is not None and column not in loaded.columns:
            raise ToolError("Unknown table column")
        matching = [
            unit
            for unit in loaded.units
            if unit.values is not None
            and all(
                key in unit.values and str(unit.values[key]) == str(value)
                for key, value in where.items()
            )
        ]
        rows = [
            {
                "document": document,
                "locator": unit.locator,
                "quote": self._snippet(unit.text),
                "values": unit.values,
            }
            for unit in matching[:limit]
        ]
        rows, bounded = self._bounded_rows(rows)
        if operation == "rows":
            return {
                "rows": rows,
                "columns": loaded.columns,
                "matched_rows": len(matching),
                "truncated": bounded or len(matching) > len(rows),
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
                loaded = self._load(citation.document)
                unit = next((u for u in loaded.units if u.locator == citation.locator), None)
                if unit is None:
                    raise ToolError("Locator does not exist in this document")
                if not citation.quote or citation.quote not in unit.text:
                    raise ToolError("Quote is absent from the specified document locator")
            except (ToolError, OSError, ValueError, UnicodeError) as exc:
                errors.append(f"citation[{index}]: {str(exc)[:200]}")
        return errors
