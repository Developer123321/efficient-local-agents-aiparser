"""Every row the model has seen, under a short ID it can cite.

The model answers with IDs such as e7; code turns them into document, locator
and exact quote. A citation therefore cannot be mistyped or invented, and
quoting costs a few output tokens instead of a copied sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hacklab.config import GuardConfig
from hacklab.forensics.decode import candidate_keys
from hacklab.guard import Compactor, scan_injection
from hacklab.models import Citation

MAX_QUOTE_CHARS = 2000
MAX_EVIDENCE = 5000
_ID = re.compile(r"e\d{1,5}")


@dataclass(frozen=True)
class Evidence:
    id: str
    source: str
    locator: str
    quote: str  # exact text at that location: what a citation carries
    shown: str  # what the model reads: compacted, flagged or derived
    flags: tuple[str, ...] = ()

    def line(self) -> str:
        warning = f" !untrusted-instructions({','.join(self.flags)})" if self.flags else ""
        return f"[{self.id}] {self.source} {self.locator}{warning} | {self.shown}"


class Ledger:
    def __init__(self, guard: GuardConfig | None = None, task_text: str = ""):
        self.guard = guard or GuardConfig()
        self.task_text = task_text
        self.compactor = Compactor(previews=self.guard.decode_previews)
        self._by_id: dict[str, Evidence] = {}
        self._by_key: dict[tuple[str, str, str], Evidence] = {}

    def __len__(self) -> int:
        return len(self._by_id)

    @property
    def keys(self) -> list[str]:
        """Key material leaked in evidence so far, for decoding later payloads."""
        return [str(key) for key in self.compactor.keys]

    def add(self, source: str, locator: str, quote: str, derived: str | None = None) -> Evidence:
        """Register one row. `derived` is text computed from it, such as a decoded payload."""
        quote = quote[:MAX_QUOTE_CHARS]
        text = quote if derived is None else derived
        key = (source, locator, text)
        if key in self._by_key:
            return self._by_key[key]
        if len(self._by_id) >= MAX_EVIDENCE:
            raise ValueError("Evidence ledger exceeds 5000 rows")
        for found in candidate_keys(text):
            if found not in self.compactor.keys:
                self.compactor.keys.append(found)
        flags: tuple[str, ...] = ()
        if self.guard.injection != "off":
            flags = tuple(scan_injection(text, self.compactor.keys))
        if flags and self.guard.injection == "withhold":
            shown = "[withheld: instruction-like text]"
        else:
            shown = self.compactor.compact(text) if self.guard.compact_secrets else text
        evidence = Evidence(f"e{len(self._by_id) + 1}", source, locator, quote, shown, flags)
        self._by_id[evidence.id] = evidence
        self._by_key[key] = evidence
        return evidence

    def get(self, evidence_id: str) -> Evidence | None:
        match = _ID.search(str(evidence_id).strip().lower())
        return self._by_id.get(match.group()) if match else None

    def citations(self, ids: list[str]) -> tuple[list[Citation], list[str]]:
        """Citations for known IDs, without repeats, and the IDs nothing was shown under."""
        cited: list[Citation] = []
        unknown: list[str] = []
        for evidence_id in ids:
            evidence = self.get(evidence_id)
            if evidence is None or not evidence.quote:
                unknown.append(str(evidence_id)[:20])
                continue
            citation = Citation(
                document=evidence.source, locator=evidence.locator, quote=evidence.quote
            )
            if citation not in cited:
                cited.append(citation)
        return cited, unknown

    def grounded(self, target: str) -> bool:
        """Does the target occur in the task or in evidence without instruction-like text?"""
        needle = target.strip().casefold()
        if not needle:
            return False
        if needle in self.task_text.casefold():
            return True
        return any(
            not evidence.flags
            and (needle in evidence.quote.casefold() or needle in evidence.shown.casefold())
            for evidence in self._by_id.values()
        )
