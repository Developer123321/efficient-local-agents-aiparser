"""Column roles read from values, for tables whose headers this code has not seen.

The event solvers know the rehearsal's headers (`agent`, `action`, `ts`,
`ticket`, ...). A real workspace may call the same fields `clinician`,
`event_code`, `encounter_ts`, `disposition` or anything else. This module
assigns a small fixed vocabulary to columns from their values and their
position, so code can still find the time, the actor, the act, its target and
the outcome.

It is deliberately conservative: a role is returned only above a confidence
threshold, and an ambiguous table yields fewer roles. Nothing here chooses an
answer, a tool or an action; it only names a column for code that computes.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# Values, not headers. An ISO time is a time whatever its column is called.
TIME = re.compile(
    r"^\s*(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?"
    r"|\d{2}[./]\d{2}[./]\d{4}[ T]\d{2}:\d{2}(?::\d{2})?)\s*$"
)
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DOMAIN = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$", re.IGNORECASE)
PATH = re.compile(r"^/[\w./-]+$")
ID = re.compile(r"^[A-Za-z][\w.-]*[-_]\d+$")
TICKET = re.compile(r"^[A-Z]{2,}-\d+$")
SNAKE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
CAMEL = re.compile(r"^[a-z]+[A-Z][A-Za-z0-9]*$")
WORD = re.compile(r"^[A-Za-z][A-Za-z .'-]{0,60}$")
INTEGER = re.compile(r"^-?\d+$")
JWT = re.compile(r"^eyJ[\w-]*\.[\w-]+\.[\w-]+$")
SECRET_PREFIX = re.compile(r"^(?:hf_|ghp_|github_pat_|sk-|AKIA|ASIA|xox[baprs]-|fake_mf_)")
STATUS = {
    "ok",
    "okay",
    "success",
    "succeeded",
    "successful",
    "failed",
    "failure",
    "error",
    "denied",
    "blocked",
    "complete",
    "completed",
    "pending",
    "active",
    "closed",
    "open",
    "revoked",
    "valid",
    "invalid",
    "approved",
    "rejected",
    "quarantined",
    "released",
    "discharged",
    "admitted",
    "yes",
    "no",
    "true",
    "false",
}
ACTION_MIN = 2
NAME_MIN = 2


@dataclass(frozen=True)
class Column:
    """One column's values, profiled from a bounded sample."""

    name: str
    total: int
    filled: int
    distinct: int
    values: tuple[str, ...]

    @property
    def ratio(self) -> float:
        return self.distinct / self.filled if self.filled else 1.0

    def rate(self, predicate: Any) -> float:
        return (
            sum(1 for value in self.values if predicate(value)) / self.filled
            if self.filled
            else 0.0
        )


def _is_id(value: str) -> bool:
    return bool(ID.fullmatch(value))


def _is_email(value: str) -> bool:
    return bool(EMAIL.fullmatch(value))


def _is_domain(value: str) -> bool:
    if not DOMAIN.fullmatch(value):
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return True
    return False


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _is_name(value: str) -> bool:
    value = value.strip()
    if EMAIL.fullmatch(value) or _is_id(value) or "@" in value:
        return False
    return bool(WORD.fullmatch(value)) and (" " in value or value.isalpha())


def _is_status(value: str) -> bool:
    return value.strip().lower() in STATUS


def _is_action(value: str) -> bool:
    return bool(SNAKE.fullmatch(value) or CAMEL.fullmatch(value))


def _is_category(value: str) -> bool:
    """A short value that names a thing: a ward, a path, a host, an address or an id."""
    return bool(
        WORD.fullmatch(value)
        or DOMAIN.fullmatch(value)
        or PATH.fullmatch(value)
        or _is_ip(value)
        or _is_id(value)
    )


def _is_secret(value: str) -> bool:
    if SECRET_PREFIX.match(value) or JWT.fullmatch(value):
        return True
    if len(value) < 24 or any(char.isspace() for char in value):
        return False
    letters = sum(char.isalpha() for char in value)
    digits = sum(char.isdigit() for char in value)
    return letters >= 8 and digits >= 4


def profile(rows: list[dict[str, str]], limit: int = 200) -> dict[str, Column]:
    """Profile every data column of a table; `_line` and other private keys are skipped."""
    if not rows:
        return {}
    sample = rows[:limit]
    found: dict[str, Column] = {}
    for key in rows[0]:
        if key.startswith("_"):
            continue
        values = tuple(str(row.get(key, "") or "").strip() for row in sample)
        filled = tuple(value for value in values if value)
        found[key] = Column(
            name=key,
            total=len(values),
            filled=len(filled),
            distinct=len(set(filled)),
            values=filled,
        )
    return found


def _best(
    columns: Iterable[Column],
    test: Any,
    *,
    exclude: set[str] | None = None,
    lowest: bool = False,
) -> Column | None:
    taken = exclude or set()
    candidates = [column for column in columns if column.name not in taken and test(column)]
    if not candidates:
        return None
    if lowest:
        return min(candidates, key=lambda column: (column.ratio, column.name))
    return max(candidates, key=lambda column: (column.filled, column.distinct, column.name))


def infer_roles(rows: list[dict[str, str]], known_ids: Iterable[str] = ()) -> dict[str, str]:
    """Map canonical roles to column names, using values and column order.

    Returned keys are a subset of: time, actor, action, target, status, ticket,
    email, name, id, secret, text. A missing role means the table did not
    support one confidently; callers fall back to the dev header names or step
    aside.
    """
    columns = list(profile(rows).values())
    if not columns:
        return {}
    order = [column.name for column in columns]
    roles: dict[str, str] = {}

    time = _best(columns, lambda column: column.rate(lambda value: bool(TIME.match(value))) >= 0.8)
    if time:
        roles["time"] = time.name
    used = {time.name} if time else set()

    email = _best(columns, lambda column: column.rate(_is_email) >= 0.6, exclude=used)
    if email:
        roles["email"] = email.name
        used.add(email.name)

    action = _best(
        columns,
        lambda column: column.rate(_is_action) >= 0.5 and ACTION_MIN <= column.distinct <= 200,
        exclude=used,
    )
    if action:
        roles["action"] = action.name
        used.add(action.name)

    status = _best(columns, lambda column: column.rate(_is_status) >= 0.6, exclude=used)
    if status:
        roles["status"] = status.name
        used.add(status.name)

    ticket = _best(
        columns,
        lambda column: column.rate(lambda value: bool(TICKET.fullmatch(value))) >= 0.6,
        exclude=used,
    )
    if ticket:
        roles["ticket"] = ticket.name
        used.add(ticket.name)

    secret = _best(columns, lambda column: column.rate(_is_secret) >= 0.6, exclude=used)
    if secret:
        roles["secret"] = secret.name
        used.add(secret.name)

    id_like = [
        column for column in columns if column.name not in used and column.rate(_is_id) >= 0.6
    ]
    person = [
        column
        for column in columns
        if column.name not in used and column.rate(_is_name) >= 0.6 and column.distinct >= NAME_MIN
    ]
    categories = [
        column
        for column in columns
        if column.name not in used
        and column.rate(lambda value: _is_category(value) and not _is_name(value)) >= 0.7
        and 2 <= column.distinct <= max(20, int(column.filled * 0.9))
    ]

    actor: Column | None = None
    target: Column | None = None
    if action:
        index = order.index(action.name)
        before = [column for column in id_like + person if order.index(column.name) < index]
        after = [column for column in categories if order.index(column.name) > index]
        overlap = set(known_ids)
        if before:
            if overlap:
                actor = max(
                    before,
                    key=lambda column: (
                        len(set(column.values) & overlap),
                        -column.ratio,
                        column.name,
                    ),
                )
            else:
                actor = min(before, key=lambda column: (column.ratio, column.name))
        if after:
            target = max(after, key=lambda column: (column.distinct, column.name))
        if actor is None and id_like:
            actor = min(id_like, key=lambda column: (column.ratio, column.name))
        if target is None:
            remaining = [column for column in categories if column is not actor]
            if remaining:
                target = max(remaining, key=lambda column: (column.distinct, column.name))
    else:
        overlap = set(known_ids)
        if overlap and id_like:
            actor = max(
                id_like,
                key=lambda column: (len(set(column.values) & overlap), -column.ratio, column.name),
            )
        elif id_like:
            # One identifier in a roster-like table: the first unique or near-unique column.
            actor = _best(id_like, lambda column: column.ratio <= 0.8, lowest=True) or id_like[0]

    if actor:
        roles["actor"] = actor.name
        used.add(actor.name)
    if target:
        roles["target"] = target.name
        used.add(target.name)

    unique = [
        column for column in id_like if column.name not in used and column.distinct == column.filled
    ]
    if unique:
        roles["id"] = max(unique, key=lambda column: (column.filled, column.name)).name
        used.add(roles["id"])
    elif actor is not None and actor.ratio == 1.0:
        roles["id"] = actor.name

    name = _best(
        person,
        lambda column: True,
        exclude=used,
    )
    if name:
        roles["name"] = name.name
        used.add(name.name)

    text = _best(
        columns,
        lambda column: (
            column.name not in used
            and column.filled
            and sum(len(value) for value in column.values) / column.filled >= 30
        ),
    )
    if text:
        roles["text"] = text.name
    return roles
