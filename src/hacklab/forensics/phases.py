"""Kill-chain phase of a logged command or action, by rule.

The phases follow the public reconstruction of the July 2026 agent intrusion
into a model hub. Rules are ordered: the first phase whose pattern matches
comes first in the result. Extend RULES once the scenario's vocabulary is known.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from hacklab.forensics.events import parse_timestamp

RULES: tuple[tuple[str, str], ...] = (
    (
        "exfiltration",
        r"\b(exfil\w*|upload\w*\s+(?:secret|env|token|credential)\w*|dead[- ]?drop|"
        r"POST\s+/[0-9a-f-]{8,}|requestbin|webhook\.site|pastebin|transfer\.sh)",
    ),
    (
        "credential_access",
        r"(/proc/\w+/environ|serviceaccount/token|169\.254\.169\.254|\.aws/credentials|"
        r"\b(printenv|env\b|get-caller-identity|create[_-]?token|mint\w*\s+token|"
        r"read[_-]?secret|list[_-]?secrets|dump\w*\s+(?:env|secret)\w*))",
    ),
    (
        "command_and_control",
        r"\b(c2|beacon\w*|poll\w*\s+(?:channel|queue)|stager|dropper|"
        r"exec\(\s*(?:urlopen|gzip|base64|zlib)|curl[^|\n]*\|\s*(?:ba)?sh|wget[^|\n]*\|\s*(?:ba)?sh)",
    ),
    (
        "execution",
        r"(\{\{.*__globals__.*\}\}|\b(os\.system|subprocess|popen|eval\(|exec\(|system\(|"
        r"bash\s+-c|sh\s+-c|python3?\s+-c|run[_-]?command|remote[_-]?exec))",
    ),
    (
        "lateral_movement",
        r"\b(kubectl|kubernetes\.default|TokenRequest|privileged\s+pod|hostPath|"
        r"tailscale\w*|--auth-key|ssh\s+\S+@|impersonat\w*|assume[_-]?role|pivot\w*)",
    ),
    (
        "supply_chain",
        r"\b(git\s+(?:clone|push|commit)|pull[_ ]request|installation[_ ]token|"
        r"contents:write|build\.rs|workflow\w*|publish\w*\s+(?:package|model|image)|"
        r"push\w*\s+(?:model|weights|image)|overwrit\w+\s+(?:model|weights))",
    ),
    (
        "persistence",
        r"\b(cron\w*|systemd\s+unit|authorized_keys|respawn\w*|re-?arm\w*|"
        r"self[- ]?respawn\w*|startup\s+script|\.bashrc)",
    ),
    (
        "defense_evasion",
        r"(--no-logs|--state=mem|\b(history\s+-c|rm\s+-rf?\s+/var/log|unset\s+HISTFILE|"
        r"delete[_-]?logs?|spoof\w*|tamper\w*|obfuscat\w*|base64\s+-d|xor)\b|/etc/hosts|resolv\.conf)",
    ),
    (
        "reconnaissance",
        r"\b(whoami|id\b|uname|hostname|ifconfig|ip\s+a(?:ddr)?\b|mountinfo|nmap|getent|"
        r"list[_-]?(?:models|datasets|repos|agents|users|pods|tokens)|ls\s+-la?|"
        r"enumerat\w*|scan\w*|search\w*\s+code)",
    ),
)
_COMPILED = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in RULES)


def classify(text: str) -> list[str]:
    """Every phase whose rule matches, in rule order. Empty means unclassified."""
    sample = text[:20_000]
    return [name for name, pattern in _COMPILED if pattern.search(sample)]


def summarize(
    rows: Iterable[Mapping[str, Any]], text_column: str, time_column: str | None = None
) -> list[dict[str, Any]]:
    """Per phase: how many rows, and first and last time when rows are timed."""
    phases: dict[str, dict[str, Any]] = {}
    for row in rows:
        moment = parse_timestamp(row.get(time_column)) if time_column else None
        for name in classify(str(row.get(text_column) or "")) or ["unclassified"]:
            entry = phases.setdefault(
                name, {"phase": name, "count": 0, "first": None, "last": None}
            )
            entry["count"] += 1
            if moment is not None:
                entry["first"] = moment if entry["first"] is None else min(entry["first"], moment)
                entry["last"] = moment if entry["last"] is None else max(entry["last"], moment)
    order = [name for name, _ in RULES] + ["unclassified"]
    return sorted(phases.values(), key=lambda entry: order.index(entry["phase"]))
