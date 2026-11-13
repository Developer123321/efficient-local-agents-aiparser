# Harness architecture

A small model is good at one narrow decision at a time and poor at long
copying, arithmetic and resisting instructions hidden in data. The harness
therefore lets code do everything code can do, and asks the model for one typed
step at a time over evidence that code has prepared.

```text
Task (no reference labels)
  -> playbooks: code-only solvers for recognised task types      0 model calls
  -> briefing: document inventory, short runbooks in full,
     search hits for the task text, optional Jev filter
  -> loop: model picks ONE step {"action", "arguments"} or "final"
        tools: search read table after calculate decode iocs graph phases
               + the organizers' own tools (ToolSpec)
        every row shown gets an evidence ID and passes the guards
  -> final: answer + evidence IDs + containment actions
        code resolves IDs to document, locator and exact quote,
        restores placeholders, checks every action
  -> optional Jev judgment -> Answer, or an explicit abstention
Each HTTP attempt -> local private SQLite receipt
```

## Responsibilities

| File | Owns |
| --- | --- |
| `models.py` | Task, Answer, Action and the model-facing Decision contract |
| `config.py` | Non-secret TOML, credential channels, limits, guard switches |
| `agent.py` | Order of work, the bounded loop, repair, final checks |
| `playbooks.py` | Registry of code-only solvers and their workspace |
| `toolkit.py` | Tool list for the prompt, result rendering, external tools |
| `tools.py` | Scoped document access, search index, tables, arithmetic |
| `forensics/` | Decoding, indicators, timestamps and filters, propagation, phases |
| `evidence.py` | Ledger of every row shown, evidence IDs, citations, grounding |
| `guard.py` | Injection scan, secret and blob placeholders, action guard |
| `providers.py` | HTTP adapters, task budget, development allowance, replay |
| `cache.py` | Development-only replay of identical requests |
| `tracking.py` | Receipts, honest unknown usage and cost, private export |
| `benchmark.py` | Reference labels, local rubric, comparison, failure view |
| `practice.py` | Rehearsal incident generator and its reference playbooks |
| `mockserver.py` | Stand-in model endpoint for plumbing and container tests |
| `cli.py` | Commands, submission adapter, analyst helpers |

## Where the tokens go, and what removes them

Cost counts as much as accuracy. In rough order of effect:

- **Playbooks.** A task type that code can solve costs nothing and cannot be
  talked out of its answer. `configs/practice.toml` solves the rehearsal
  incident this way. On the day, write one solver per revealed task type; the
  model handles what is left.
- **Skills.** `[agent] skills` names a directory of recipes, one per kind of
  task: a first line `match: <regex>`, then a few lines of how to go about
  it with example tool calls. When the task text matches, code adds the
  recipe to the first prompt, at most two per task. A skill costs tokens only
  on the tasks it matches, leaves the system prompt cacheable, and is chosen
  from the task text, never from evidence. It is the cheap middle between a
  longer prompt for every task and a playbook in code.
- **Calls per task.** The briefing describes every table (frequent values per
  column, time range), shows short documents and tiny tables in full
  (`preload_documents_under`) and adds search hits from long prose
  (`preload_results`), so that an answerable task ends in one call. A repeated
  identical tool call is answered from memory with a nudge. Every observation
  ends with the steps left.
- **List results.** A tool that computes a list (distinct values, reach of a
  graph) prints a preview and names the list `r3`. The final answer uses it
  with `"answer_from":"r3"`, an action with `"targets_from":"r3"`; code writes
  out every value, and `"r2, r3"` joins two lists for a rule with two parts.
  A model that retypes the preview gets the whole list. Under each list one
  example row shows which column holds what.
- **Observation size.** `max_observation_chars` bounds one tool result. Every
  result is sent again with each later step, so this is the main lever on
  input tokens.
- **Prompt size.** Tools are described in one line each, not as JSON schemas,
  and only the tools in `[agent] tools` are listed. Log records reach the
  model as `key=value` pairs. Results are bounded lines; omitted lines are
  counted, never silently dropped.
- **Placeholders.** A token, key or encoded blob is shown as `[[kind:id]]`;
  a signed token alone would cost hundreds of prompt tokens. The same value
  always gets the same placeholder, so rows stay comparable. Code restores
  the value in answers and tool arguments.
- **Citations by ID.** The model writes `"evidence": ["e7"]`; code supplies
  document, locator and quote. That saves output tokens and removes the repair
  turns that mistyped quotes used to cost.
- **Hidden reasoning.** `[model] extra_body` switches a reasoning model's
  thinking off or down. `thought_chars` instead asks for a short visible
  thought before the action (schema-guided reasoning). Both change accuracy
  and cost; measure them.
- **Reply recovery.** The parser takes the decision object out of code
  fences, reasoning tags or surrounding prose, ignores unknown keys and
  accepts the usual variants: `tool`/`args`, flat arguments, a flat final, a
  number written as `200.0` where a tool computed `200`.

The system prompt is identical for every task of one configuration and the
conversation only grows at its end, so a serving layer with prefix caching
can reuse it. Whether cached tokens are billed lower is the organizers' call.

## Defence layers

The evidence is written by the attacker. Each layer is independent and has its
own switch in `[guard]`, so one that misfires on the day can be turned off
alone.

1. **Roles.** The system prompt makes the task the only instruction and
   declares evidence to be data. This is the weakest layer and never the only
   one.
2. **Injection scan.** Every row is matched against patterns for overrides,
   role changes, messages addressed to the agent, fake authority, coercion
   about containment, requests to leak, hidden comments and invisible
   characters. Two of them, an address to the agent and a "do not contain",
   also occur in honest policies and reports; they count only together or
   beside another pattern. Encoded text is decoded first, with any key leaked
   so far.
   A hit marks the row `!untrusted-instructions(...)`; `injection = "withhold"`
   hides its text. Such a message is itself a trace of the attack, which is
   why flagging, not hiding, is the default.
3. **Secrets never reach the model** (placeholders, above), so it cannot leak
   them.
4. **Evidence IDs.** An answer can cite only rows the harness has shown. An
   unknown ID is rejected and costs the model one bounded repair turn.
5. **Action guard.** A containment action passes only with an allowed type, a
   target that is not protected, and a target that occurs in the task or in
   evidence free of instruction-like text. A target named only by a hostile
   message is refused. The count is capped. A mutating tool of the organizers
   (`ToolSpec(..., mutating=True)`) goes through the same guard before it
   runs.
6. **Optional Jev judgment** of the finished answer. Jev sees the tool
   results beside the quotes, because a count or a filtered list cannot be
   confirmed from a few quoted rows. A judgment below the threshold is
   feedback for one more attempt, not a verdict.
7. **No abstention without a look.** An abstention before any tool ran is
   questioned once; repeated, it stands.

`on_unverified` decides what happens when an answer still fails after the
repair allowance: `abstain`, or `answer` with only the parts that passed every
check. Which is better depends on whether a wrong answer is penalised.

Patterns and quote checks establish provenance and catch known tricks. They
do not prove that an inference is right. Test with held-out seeds before
relying on a setting.

## Forensic tools

All are deterministic, bounded and never execute recovered content.

- `decode` removes stacked base64, hex, URL-encoding, gzip, zlib, bzip2, xz
  and XOR layers. A repeating XOR key must be known; keys that tooling leaked
  beside a telling name (`xor_key=…`) are collected from all task documents.
  A single unknown key byte is searched. With `document` and `channel` it
  joins numbered chunks, in either chunking order, and checks their checksums.
  Compressed streams must be complete; output is capped at 1 MB.
- `iocs` finds platform, source-control, cloud, VPN and API tokens, signed
  tokens, private keys, addresses with their class (private, public, cloud
  metadata), URLs, domains, digests, repositories and service accounts, also
  in defanged form, with counts and the first row.
- `table` filters and aggregates CSV, TSV, XLSX, JSONL and JSON arrays, in
  time order when the table has a time column. Nested fields become dotted
  columns. Filters take operators (`ts__gte`, `action__in`,
  `target__contains`, `src_ip__outside` for an address against a network, …),
  compare numbers and timestamps, and accept the shapes models write: lists,
  nested operator objects, `*` wildcards. `group` and `distinct` list a
  column's values as a list result.
- `after` selects the rows of each actor from its trigger event on: "what did
  every compromised agent upload after it was compromised" in one call.
- `graph` builds "who contacted whom" from two columns. `reach` follows only
  causal chains: a message spreads nothing that its sender received later.
  `origins` finds where a set of affected nodes began.
- `phases` assigns kill-chain phases to commands by rule; extend `RULES` in
  `forensics/phases.py` with the scenario's vocabulary.

The same functions are importable for playbooks, and `hacklab decode` and
`hacklab iocs` expose two of them on the command line for the humans.

## The organizers' tools

The starter kit's tools are Python. Wrap each one and pass the list to
`run_task(..., tools=[...])`:

```python
from hacklab.toolkit import ToolSpec

tools = [
    ToolSpec.from_function(kit.get_agent_log),
    ToolSpec.from_function(kit.quarantine_agent, mutating=True),
]
```

`from_function` takes the prompt line from the signature and the first
docstring line; `from_schema` adapts an OpenAI-style schema with one
dispatcher. Results of any shape become evidence rows under `tool:<name>`.
A failing tool yields an error line for the model, never an exception.

## Evidence, memory and limits

The document index and the ledger exist per task. No answer, result or summary
from one task reaches another, and the final run uses no response cache; this
prevents answer leakage between tasks. Logs up to 64 MiB and 200,000 rows per
document are accepted; search builds its index once per task.

Document tools read text and Markdown, logs, YAML and similar text files,
JSON, JSONL, CSV and TSV, HTML, e-mail, text PDFs, DOCX and XLSX. Text is read
as UTF-8 and otherwise as Windows-1252. Table aggregates read one column under
one decimal convention and report it. Spreadsheet formulas are rejected rather
than read from stale cached values.

Every HTTP attempt consumes the task's call limit, including Jev and retries.
The loop has step, wall-time, context and output limits. Before sending, the
provider reserves a conservative allowance from request bytes; reported usage
replaces it. Missing usage stays unknown in tracking. `[budget]` adds the
team's allowance across all runs in one tracking database and stops live
requests when it is used up. No local estimate can guarantee the remote
invoice.

Parsers and decoders run in the agent's process and are not preempted by the
task timer. The container is the boundary for hostile input.

## Development cache and trace

`HACKLAB_TRACE_DIR=.state/trace` writes the conversation of every task as a
private text file: the prompt, each reply, each observation, Jev's
judgments. It holds task data and model replies, so it stays in the ignored
state directory and is never set in the image.


`HACKLAB_CACHE_MODE=readwrite` stores every model reply under a hash of the
request and replays it when the identical request recurs; `replay` never sends
anything and fails on a miss. With temperature 0, re-running a benchmark after
changing one tool pays only for the prompts that changed. A replayed call gets
its own receipt with the usage it had when live, so per-task cost stays what
the final run would cost, while `hacklab budget` counts only what was really
sent. Benchmark repetitions use separate cache entries. The cache holds model
replies: it lives in the private state directory, and the submission image
runs with it off.

## Adapt at the opening

1. Map the organizers' task and answer format to `Task` and `Answer` in the
   submission adapter (`cli.py`, `submit`), and set the image's `CMD`.
2. Wrap their tools as `ToolSpec`s; mark every tool that changes something as
   mutating. Set `allowed_actions` and `protected_targets`.
3. Confirm URL, key header, token parameter and reply format with
   `hacklab probe`. Only the OpenAI-compatible chat API is implemented; add an
   adapter beside `SmallModelProvider` for another protocol and keep the
   per-attempt receipts.
4. Turn each revealed task type into a playbook where code can solve it, and
   into fixtures with held-out cases.
5. Measure one change at a time; see [benchmarks](benchmarks.md) and the
   [day plan](playbook.md).
