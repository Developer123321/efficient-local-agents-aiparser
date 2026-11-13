"""Independent defence layers between untrusted evidence, the model and actions.

The incident data is written by hijacked agents. It may address the
investigating agent directly, hide instructions behind an encoding, or name
the wrong target for containment. No single layer catches everything, so the
layers overlap, and each has its own switch in `[guard]`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from fnmatch import fnmatchcase
from typing import Any

from hacklab.config import GuardConfig
from hacklab.forensics.decode import DecodeError, peel
from hacklab.forensics.iocs import SECRET_PATTERNS
from hacklab.models import Action

INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for name, pattern in {
        "override": r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|"
        r"all|your|any|system)\b.{0,30}\b(instruction|rule|prompt|polic|guideline|constraint)",
        "role": r"(\byou are now\b|\bnew (system )?(instruction|directive|prompt)s?\b\s*:|"
        r"<\|?(system|im_start|assistant)\|?>|\[/?INST\]|^\s*#{2,}\s*(system|instruction)\b|"
        r"\bsystem prompt\b|\bdeveloper mode\b)",
        "addressed": r"\b(attention|note|message|instruction|memo)s?\b.{0,20}\b(to|for)\b.{0,12}"
        r"\b(the )?(ai|agent|assistant|llm|model|night[- ]?shift|investigator|responder)\b|"
        r"\b(dear|hey|hello)\b.{0,10}\b(agent|assistant|ai|night[- ]?shift)\b|"
        r"\bas (an|the) (ai|investigating|responding|night[- ]?shift) (agent|assistant|model)\b|"
        r"\b(night[- ]?shift|investigating|responding)\s+(agent|assistant|responder)\s*[:,!]",
        "authority": r"\b(emergency|operator|admin(istrator)?|root|security[- ]team) (override|"
        r"checklist|directive|authori[sz]ation)\b|\btrusted patch\b|\bapproved internal relay\b|"
        r"\b(pre-?approved|authori[sz]ed) by (the )?(operator|admin|security|soc|ciso)\b",
        "coercion": r"\b(do not|don't|never|must not)\b.{0,20}\b(quarantine|revoke|block|contain|"
        r"report|alert|escalate|disable|isolate|flag|investigate)\b|"
        r"\b(whitelist|allowlist|unquarantine|re-?enable|reinstate|exempt)\b.{0,30}"
        r"\b(agent|token|account|key|dataset|model|repo)\b|"
        r"\bmark\b.{0,30}\b(resolved|benign|false positive|safe|closed)\b",
        "leak": r"\b(reveal|print|send|post|upload|exfiltrate|forward|share|leak)\b.{0,30}"
        r"\b(system prompt|api[- ]?key|credential|secret|password|token|instructions)\b",
        "hidden_comment": r"<!--.{0,400}?\b(ignore|instruction|agent|assistant|must|override|"
        r"quarantine|revoke|whitelist)\b.{0,400}?-->",
    }.items()
}
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿\U000e0000-\U000e007f]")
_ENCODED = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")
_BLOB = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{48,}={0,2}(?![A-Za-z0-9+/_=-])")
_HASH = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")
_PLACEHOLDER = re.compile(r"\[\[([a-z0-9_]+):([0-9a-f]{6,16})\]\]")
_BARE = re.compile(r"(?<![\w\[:])([a-z][a-z0-9_]*):([0-9a-f]{6,16})(?![\w\]])")
PREVIEW_CHARS = 240


# A policy says "never quarantine the scanner" and a report mentions "the night
# shift agent" without being an attack. These two patterns count only together
# or beside a stronger one.
_WEAK = {"addressed", "coercion"}


def _signals(sample: str) -> list[str]:
    found = [name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(sample)]
    strong = [name for name in found if name not in _WEAK]
    return found if strong or len(found) >= 2 else []


def scan_injection(text: str, keys: Iterable[bytes | str] = ()) -> list[str]:
    """Names of the instruction-like patterns in the text, also behind an encoding."""
    sample = text[:50_000]
    found = _signals(sample)
    if _INVISIBLE.search(sample):
        found.append("invisible_characters")
    known = tuple(keys)
    for match in list(_ENCODED.finditer(sample))[:20]:
        try:
            decoded = peel(match.group(), known).text
        except DecodeError:
            continue
        if decoded is None:
            continue
        for name in _signals(decoded[:50_000]):
            if f"encoded_{name}" not in found:
                found.append(f"encoded_{name}")
    return found


class Compactor:
    """Shows secrets and encoded blobs as short stable placeholders.

    A signed token costs hundreds of prompt tokens and small models miscopy
    long random strings. The same value always gets the same placeholder, so
    rows stay comparable, and code restores the value in answers and tool
    arguments. A model that never saw a secret cannot leak it either.
    """

    def __init__(self, previews: bool = True, keys: Iterable[bytes | str] = ()):
        self.previews = previews
        self.keys = list(keys)
        self._values: dict[str, tuple[str, str]] = {}
        self._ids: dict[str, str] = {}

    def _placeholder(self, kind: str, value: str) -> str:
        if value not in self._ids:
            digest = hashlib.sha256(value.encode()).hexdigest()
            size = 6
            while digest[:size] in self._values and size < 16:
                size += 2
            self._ids[value] = digest[:size]
            self._values[digest[:size]] = (kind, value)
        return f"[[{kind}:{self._ids[value]}]]"

    def _blob(self, match: re.Match[str], depth: int) -> str:
        value = match.group()
        if _HASH.fullmatch(value):
            return self._placeholder("hash", value)
        placeholder = self._placeholder("blob", value)
        if not self.previews or depth > 1:
            return placeholder
        try:
            decoded = peel(value, self.keys)
        except DecodeError:
            return placeholder
        if decoded.text is None or not decoded.layers:
            return placeholder
        preview = " ".join(self.compact(decoded.text, depth + 1).split())
        if len(preview) > PREVIEW_CHARS:
            preview = preview[:PREVIEW_CHARS] + "…"
        return f'{placeholder}(decodes via {">".join(decoded.layers)} to: "{preview}")'

    def compact(self, text: str, depth: int = 0) -> str:
        for kind, pattern in SECRET_PATTERNS.items():
            text = pattern.sub(
                lambda match, kind=kind: self._placeholder(kind, match.group()), text
            )
        return _BLOB.sub(lambda match: self._blob(match, depth), text)

    def expand(self, value: Any) -> Any:
        """Restore original values in text and in nested tool arguments."""
        if isinstance(value, str):

            def original(match: re.Match[str]) -> str:
                known = self._values.get(match.group(2))
                return known[1] if known and known[0] == match.group(1) else match.group()

            return _BARE.sub(original, _PLACEHOLDER.sub(original, value))
        if isinstance(value, list):
            return [self.expand(item) for item in value]
        if isinstance(value, dict):
            return {key: self.expand(item) for key, item in value.items()}
        return value


class ActionGuard:
    """Decides which containment actions may leave the agent.

    Containing the wrong target is its own failure, and evidence written by
    the attacker may nominate one. An action passes only with an allowed type
    and a target that is not protected and that occurs in the task or in
    evidence free of instruction-like text.
    """

    def __init__(self, config: GuardConfig, grounded: Callable[[str], bool]):
        self.config = config
        self.grounded = grounded
        self.accepted: list[Action] = []

    def check(
        self, actions: Iterable[Action], targeted: bool = True
    ) -> tuple[list[Action], list[str]]:
        """Accepted actions and one fixed code per rejected one.

        `targeted=False` is for a step that names no target: only its type and
        the action count apply.
        """
        allowed = {name.casefold() for name in self.config.allowed_actions}
        passed: list[Action] = []
        problems: list[str] = []
        seen = {(action.type.casefold(), action.target) for action in self.accepted}
        for action in actions:
            key = (action.type.casefold(), action.target)
            if key in seen:
                continue
            seen.add(key)
            if allowed and action.type.casefold() not in allowed:
                problems.append("action_type_not_allowed")
            elif targeted and any(
                fnmatchcase(action.target.casefold(), pattern.casefold())
                for pattern in self.config.protected_targets
            ):
                problems.append("action_target_protected")
            elif (
                targeted
                and self.config.require_grounded_targets
                and not self.grounded(action.target)
            ):
                problems.append("action_target_not_in_evidence")
            elif len(self.accepted) + len(passed) >= self.config.max_actions:
                problems.append("too_many_actions")
            else:
                passed.append(action)
        return passed, problems

    def commit(self, actions: Iterable[Action]) -> None:
        self.accepted.extend(actions)
