"""Timestamps and row analytics for heterogeneous logs."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

TIME_FIELDS = ("ts", "time", "timestamp", "@timestamp", "datetime", "date", "created_at", "when")
_FORMATS = (
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%d/%b/%Y:%H:%M:%S %z",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%Y-%m-%d",
)
_SYSLOG = re.compile(r"^([A-Z][a-z]{2}) +(\d{1,2}) (\d{2}:\d{2}:\d{2})$")
_OPERATORS = ("contains", "prefix", "suffix", "ne", "gte", "lte", "gt", "lt", "in", "re")


def parse_timestamp(value: Any, default_year: int | None = None) -> datetime | None:
    """Read the usual log timestamp shapes as an aware UTC datetime, or None.

    Text without an offset is taken as UTC. Numbers are Unix seconds, or
    milliseconds when they are too large for seconds.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)) or re.fullmatch(r"\d{9,13}(\.\d+)?", str(value).strip()):
        number = float(value)
        if number > 1e11:
            number /= 1000
        # Small numbers are counters or sizes, not moments after 1973.
        if not 1e8 <= number < 4e9:
            return None
        return datetime.fromtimestamp(number, UTC)
    else:
        text = str(value).strip()
        if not text or len(text) > 64:
            return None
        moment = None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for pattern in _FORMATS:
                try:
                    moment = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
        if moment is None and default_year is not None and (match := _SYSLOG.match(text)):
            try:
                moment = datetime.strptime(
                    f"{default_year} {match[1]} {match[2]} {match[3]}", "%Y %b %d %H:%M:%S"
                )
            except ValueError:
                return None
        if moment is None:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def flatten(value: Any, prefix: str = "", limit: int = 100) -> dict[str, Any]:
    """Nested objects as dotted column names, so every field can be filtered."""
    flat: dict[str, Any] = {}

    def visit(item: Any, name: str) -> None:
        if len(flat) >= limit:
            return
        if isinstance(item, Mapping) and item:
            for key, child in item.items():
                visit(child, f"{name}.{key}" if name else str(key))
        elif isinstance(item, (list, tuple, dict)):
            flat[name] = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        else:
            flat[name] = item

    visit(value, prefix)
    return flat


def time_field(columns: Iterable[str]) -> str | None:
    lowered = {column.lower(): column for column in columns}
    return next((lowered[name] for name in TIME_FIELDS if name in lowered), None)


def _number(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _ordered(left: Any, right: Any) -> tuple[Any, Any] | None:
    """Both sides as numbers, else as timestamps, else as text."""
    numbers = (_number(left), _number(right))
    if None not in numbers:
        return numbers
    moments = (parse_timestamp(left), parse_timestamp(right))
    if None not in moments:
        return moments
    return (str(left), str(right)) if left is not None and right is not None else None


def where_column(key: str) -> str:
    """The column a filter key refers to: `size__gt` filters the column `size`."""
    column, _, operator = key.rpartition("__")
    return column if column and operator in _OPERATORS else key


def matches(row: Mapping[str, Any], where: Mapping[str, Any]) -> bool:
    """Conditions are ANDed. `column` tests equality; `column__op` uses an operator.

    Operators: contains, prefix, suffix, ne, gt, gte, lt, lte, in, re. Text
    comparisons ignore case; ordering compares numbers, then timestamps.
    """
    for key, expected in where.items():
        column = where_column(key)
        operator = key[len(column) + 2 :] if column != key else "eq"
        actual = row.get(column)
        text = "" if actual is None else str(actual).casefold()
        if operator == "in":
            options = expected if isinstance(expected, (list, tuple)) else [expected]
            ok = text in {str(option).casefold() for option in options}
        elif operator in {"gt", "gte", "lt", "lte"}:
            pair = _ordered(actual, expected)
            if pair is None:
                return False
            left, right = pair
            ok = {"gt": left > right, "gte": left >= right, "lt": left < right}.get(
                operator, left <= right
            )
        elif operator == "re":
            try:
                ok = re.search(str(expected)[:200], str(actual or ""), re.IGNORECASE) is not None
            except re.error:
                return False
        else:
            wanted = str(expected).casefold()
            ok = {
                "eq": text == wanted,
                "ne": text != wanted,
                "contains": wanted in text,
                "prefix": text.startswith(wanted),
                "suffix": text.endswith(wanted),
            }[operator]
        if not ok:
            return False
    return True


def count_by(rows: Iterable[Mapping[str, Any]], column: str) -> list[tuple[str, int]]:
    """Values of one column by frequency, ties in value order."""
    counts = Counter("" if row.get(column) is None else str(row[column]) for row in rows)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def first_last(
    rows: Iterable[Mapping[str, Any]], column: str, time_column: str
) -> dict[str, dict[str, Any]]:
    """Per value of `column`: when it first and last appeared, and how often."""
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        moment = parse_timestamp(row.get(time_column))
        if moment is None or row.get(column) in (None, ""):
            continue
        entry = seen.setdefault(str(row[column]), {"first": moment, "last": moment, "count": 0})
        entry["first"] = min(entry["first"], moment)
        entry["last"] = max(entry["last"], moment)
        entry["count"] += 1
    return dict(sorted(seen.items(), key=lambda item: (item[1]["first"], item[0])))


def bursts(
    rows: Iterable[Mapping[str, Any]], time_column: str, window_seconds: int, threshold: int
) -> list[dict[str, Any]]:
    """Fixed windows holding at least `threshold` rows, in time order."""
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    windows: Counter[int] = Counter()
    for row in rows:
        moment = parse_timestamp(row.get(time_column))
        if moment is not None:
            windows[int(moment.timestamp()) // window_seconds] += 1
    return [
        {
            "start": datetime.fromtimestamp(index * window_seconds, UTC),
            "end": datetime.fromtimestamp(index * window_seconds, UTC)
            + timedelta(seconds=window_seconds),
            "count": count,
        }
        for index, count in sorted(windows.items())
        if count >= threshold
    ]
