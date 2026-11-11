# Harness architecture

The small generator chooses one JSON decision at a time. Python owns the loop,
budgets, tool dispatch, parsing, arithmetic, output validation and receipts.
Jev can judge two independent final-answer predicates in a single native
System One request. It never generates code, prose or arbitrary tool arguments.

```text
Task (no reference labels)
  -> bounded JSON planner -> search / read / table / calculate
  -> exact document + locator + quote checks
  -> optional batched Jev grounding + constraints judgment
  -> Answer, or explicit abstention
Each HTTP attempt -> local private SQLite receipt
```

## Responsibilities

| File | Owns |
| --- | --- |
| `models.py` | Strict Task, Citation, Decision and Answer contracts |
| `config.py` | Non-secret TOML, runtime credential channels and validated limits |
| `tools.py` | Scoped document access, retrieval, decimal arithmetic and tables |
| `providers.py` | Explicit HTTP contracts, retry accounting and task budgets |
| `agent.py` | Short planning loop, bounded repair, citation and Jev gates |
| `tracking.py` | Metadata receipts, honest unknown usage/cost and private export |
| `benchmark.py` | Separate reference labels, deterministic practice rubric and comparison |
| `cli.py` | Local readiness, run/benchmark/inspection and JSONL adapter |

## Evidence and memory

The document index/cache is created for each task. No answer, result or summary
from a previous task is reused. The live harness has no response cache and no
cross-task model memory; this prevents answer leakage and stale results during
evaluation. Exact document hashes identify parsed input. Short, recent
decision/observation pairs form working memory within the task and are dropped
when the configured context-character allowance is reached.

Document tools accept UTF-8 text/Markdown/JSON, comma-delimited CSV, text PDFs,
DOCX and XLSX. They enforce byte, expansion, row/unit, response and quote limits.
Scanned PDFs need an explicit offline OCR preprocessing step; they are not
silently interpreted as empty text. Spreadsheet formulas are rejected so that
stale cached values are not mistaken for authoritative numbers. Numeric table
values use a decimal point without currency or thousands separators; normalize
locale-specific input explicitly and preserve the original evidence.

An exact quote check proves that the cited string exists at that location. It
does not prove that an inference follows, that an uncited conflicting document
was considered, or that a timestamp establishes authority. These are separate
tasks for task-specific rules, held-out evaluation and optionally Jev. Prompt
instructions alone are not a guarantee against prompt injection.

## Budget behavior

Every HTTP attempt consumes the common call limit, including Jev and retries.
The loop has step, wall-time, context and output limits. Before sending a
request, the provider reserves a conservative allowance based on request bytes
and requested output; this allowance is not a measured token count. Returned
usage replaces it for local accounting. Missing usage consumes the allowance
locally and remains unknown in tracking. A configured cost cap requires explicit
prices for every live provider. No application-side estimate can guarantee the
remote invoice after an ambiguous timeout or unreported provider overhead.

HTTP timeouts and limits bound normal model work. Document parsers run in the
same process and are not forcibly preempted by the task timer. Use the dedicated
VM boundary for untrusted documents; the parser policy is not an OS sandbox.

## Adapt at the opening

1. Map the actual organizer task/input/output formats to Task/Answer. The local
   `submit` JSONL interface is a preparation adapter, not an official integration.
2. Confirm the small model's URL, auth, token parameter and response format. Only
   the generic OpenAI-compatible API is implemented today. Add a provider adapter
   if the event uses another protocol; preserve per-attempt receipts.
3. Encode the revealed scenario's deterministic validity and precedence rules
   in code. Keep semantic judgments atomic and choices small.
4. Evaluate retrieval depth, context size, step limits and the optional Jev
   gate. The starting values are uncalibrated; keep a change only when it improves
   held-out accuracy/cost for the actual supplied model.
5. Keep coding-assistant APIs separate. Bob builds the code; it does not answer
   competition tasks on behalf of either supplied model.
