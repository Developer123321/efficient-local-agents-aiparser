# How the agent works

A small model is good at one narrow decision at a time. It is poor at copying
long lists, at arithmetic and at resisting instructions hidden in data. So
code does what code can do, and the model picks one typed step at a time over
evidence that code has prepared.

```text
Task
  -> playbooks   code solves a task type it recognises          0 model calls
  -> briefing    what the documents hold, short ones in full,
                 a matching skill, search hits for the task text
                 (optionally ordered by the embedding model)
  -> loop        the model picks ONE tool call, or "final"
                 every row it sees gets an evidence ID and passes the guards
  -> final       answer + evidence IDs + containment actions
                 code turns IDs into quotes and checks every action
  -> Jev         optional second opinion on the finished answer
  -> Answer      always, also after an error
```

## Files

| File | Owns |
| --- | --- |
| `agent.py` | Order of work, the loop, repair, final checks |
| `event/` | The event: the Task API client and file mirror (`arena.py`), one run (`arena_run.py`), the rules for the model, playbooks, skills |
| `playbooks.py` | Registry of code-only solvers and the workspace they work in |
| `toolkit.py` | Tool list for the prompt, result rendering, the organizers' tools |
| `tools.py` | Document access (text, PDF, DOCX, ODT, XLSX, ODS), search, tables, arithmetic |
| `forensics/` | Decoding, indicators, timestamps, propagation, attack phases |
| `evidence.py` | Every row shown to the model, its ID, citations |
| `guard.py` | Injection scan, placeholders for secrets, action guard |
| `providers.py` | Calls to the three models (HTTP, or the kit's own client), retries, budgets, replay cache |
| `tracking.py` | A receipt for every request |
| `benchmark.py` | Scoring, comparison, failure view |
| `practice.py` | Rehearsal incident and its playbooks |
| `cli.py` | Commands: `arena run` for the event, `submit` for a task file |
| `config.py`, `models.py` | Settings and the data shapes |

## Where tokens go, and what saves them

Cost is half the score. Best lever first:

| Lever | What it does | Setting |
| --- | --- | --- |
| Playbook | Code answers the task; no tokens, immune to injected text | `[agent] playbooks` |
| Skill | A short recipe, added only to tasks whose text matches | `[agent] skills` |
| Briefing | Tables are described and short documents shown up front, so an easy task ends in one call | `preload_documents_under`, `preload_results` |
| List results | A computed list is named `r3`; the answer says `"answer_from":"r3"` and code writes out every value | `list_preview` |
| Observation size | Every tool result is sent again with each later step | `max_observation_chars` |
| Tool list | Each listed tool costs prompt tokens on every call | `[agent] tools` |
| Placeholders | A long token or blob appears as `[[kind:id]]`; code restores it | `[guard] compact_secrets` |
| Citations by ID | The model writes `"evidence":["e7"]`; code supplies the quote | always on |
| Reasoning | Hidden thinking is paid output | `[model] extra_body` |
| Early stop | After two calls that found nothing new, the model is told to finish | always on |

A skill file starts with `match: <regex>` and then holds a few lines with
example tool calls. Code picks it from the task text, never from the evidence.
At most two skills are added per task.

## Defence layers

The attackers wrote the evidence. Each layer works alone and has its own
switch in `[guard]`.

1. **Roles.** The task is the only instruction; evidence is data.
2. **Injection scan.** Rows with instruction-like text are marked, also
   behind base64 and similar encodings. `injection = "withhold"` hides them.
3. **Secrets stay out of the prompt.** The model sees placeholders, so it
   cannot leak a token.
4. **Evidence IDs.** An answer can only cite rows the harness has shown.
5. **Action guard.** A containment action needs an allowed type, a target
   that is not protected, and a target that appears in the task or in clean
   evidence. A target named only by a hostile message is refused.
6. **No abstention without a look.** An abstention before any tool ran is
   questioned once.

`on_unverified` decides what happens to an answer that still fails the
checks: `abstain`, or `answer` with only the parts that passed.

These layers catch known tricks. They do not prove that an answer is right.

## Jev at run time

Jev is optional in three roles: judge of the finished answer, filter for
preloaded evidence (`[jev] select`), or a `choose` call inside a playbook.

It never blocks an answer. If Jev has no key, cannot be reached or runs out of
allowance, the answer that passed the code's checks stands. After three failed
requests in a row the run stops asking Jev. `TYPESAFE_BASE_URL` points it at a
gateway.

## The embedding model at run time

Qwen3 Embedding is the third provided model. With `[embedding] enabled =
true` (`configs/embed-rank.toml`) the first prompt's search works in two
steps: the lexical search returns `candidates` hits instead of
`preload_results`, one embedding request scores them against the task text,
and the `preload_results` most similar ones go into the prompt. The prompt
does not grow; the request costs input tokens of the embedding model only.

- It orders rows that code found. It adds no row, and it never selects a
  tool, a target or an action. The rows pass the injection scan as before.
- It never blocks an answer. Without a model name, a key or a route, or after
  three failed requests in a row, the lexical order stands. A request waits
  at most 15 seconds.
- It is asked only when the search finds more than `preload_results` hits
  outside tables and outside documents shown in full. On the rehearsal
  incident and on `bench/smoke.jsonl` that never happens: their evidence is
  tables and short documents. Expect an effect only on long prose.
- The task text carries the instruction (`[embedding] instruction`), the
  passages do not, as the model card asks.
- `EMBEDDING_MODEL_NAME` names the model. `EMBEDDING_BASE_URL` and
  `EMBEDDING_API_KEY` are only needed when it is not served next to the small
  model: unset, the small model's address and key are used.

Whether this helps is unmeasured. The `search` tool inside the loop stays
lexical.

## Tools the model can call

| Tool | Does |
| --- | --- |
| `search`, `read` | Find and read text in the task's documents |
| `table` | Filter, count, group and list distinct values in CSV, JSONL, JSON, XLSX |
| `after` | Rows of each actor from its trigger event on ("what did every compromised agent do afterwards") |
| `graph` | Who contacted whom; reach of an infection; where it began |
| `decode` | Peel base64, hex, gzip, XOR and similar layers; join numbered chunks |
| `iocs` | List tokens, keys, addresses, URLs, digests in a document |
| `phases` | Assign attack phases to commands by rule |
| `calculate` | Arithmetic |

All are deterministic and bounded, and none executes recovered content.
`hacklab decode` and `hacklab iocs` offer two of them on the command line.

## What counts as a table

The data's shape is unknown until the opening, so a document is read by what
it holds, not by its name.

| Document | Read as |
| --- | --- |
| `.jsonl`, `.ndjson`, and a `.log`, `.txt` or suffix-less file of JSON lines | One row per line, nested fields as `a.b` columns |
| A log of `key=value` pairs, with or without a leading timestamp and level | One row per line |
| `.csv`, `.tsv` | Rows; a short row gets empty cells, a long one keeps its surplus in the last column, unnamed or repeated headers get names |
| `.json` with one list of records | Rows |
| `.json` with several lists, or records stored under their IDs | All rows, told apart by a `_table` column; the ID is `_key` |
| Anything else that is text | Lines to read and search |

The time column is found by its usual names, else by its values: the column
whose every value is a timestamp. A damaged line stays readable as text. A
binary file is refused, not guessed at.

## The event's platform

`hacklab arena run` ([arena_run.py](../src/hacklab/event/arena_run.py)) is
what the submission image executes. Per run: read the tool list, start or
continue the run, work the tasks easy level first on a few threads, finish.
Per task:

1. **Mirror.** [`Mirror`](../src/hacklab/event/arena.py) fetches the run's
   manifest once and every distinct file once by its SHA-256, then copies
   the task's files into a directory of its own. Our `search`, `read` and
   `table` work there on every format; the platform's `read` and `search`
   are not offered. Without a manifest the task's `workspace.zip` is used;
   without either, the platform's own tools.
2. **Question.** The instruction, then `/AGENTS.md` quoted as the operator's
   rules, then the names of the operator files it mentions. The system
   prompt ends with the event's standing rules
   ([arena-rules.md](../src/hacklab/event/arena-rules.md), `[agent] rules`).
3. **Tools.** The platform's tools are wrapped as `ToolSpec`s; their results
   pass the ledger like any other. `query_logs`, `access_path`,
   `threat_intel` and `get_containment` only look. Every other platform tool
   counts as one that changes something: it is offered only when the
   instruction contains a word of action (`ACTION_WORDS`), it passes the
   action guard, `send_message` is refused for a recipient that neither the
   instruction, the operator files nor a CSV registry names, `write` outside
   `/drafts/` is refused, and URLs in what is sent are defanged.
4. **Answer.** `to_arena` turns the `Answer` into `{outcome, answer,
   sources}`: the typed value (a list stays a list), one of the four
   outcomes, and each citation as path plus one locator in the platform's
   terms (`line`, `record`, `sheet` and `row`, `paragraph`, `page`), at most
   ten. URLs in the answer are defanged.
5. **Submit.** Always, also after a local failure: then with an empty `OK`
   answer and the cause in `.state/arena/runs/RUN/TASK.json`.

A tool can be wrapped by hand the same way:

```python
from hacklab.toolkit import ToolSpec

tools = [ToolSpec("containment", "containment(ops): ...", handler, mutating=True)]
run_task(task, root, config, tracker, tools)
```

`mutating=True` sends the call through the action guard first. A failing tool
gives the model an error line, never an exception. A playbook calls a
platform tool with `workspace.call("containment", ops=[...])`; a mutating
tool passes the action guard there too.

With `OPENROUTER_API_KEY` in the environment, `config._openrouter` points all
three models at `OPENROUTER_BASE_URL` (default: OpenRouter itself) under the
event's model names, whatever the profile's own addresses say; Jev is then asked
at `/api/alpha/decisions`.

## Limits and privacy

- Each task has limits for steps, calls, time, context and output tokens
  (`[limits]`). Every HTTP attempt counts, including Jev and retries. The
  defaults in `config.py` equal the profile the benchmark numbers come from.
  `configs/live.toml` differs from them in the fields that
  `tests/test_config.py` lists, each with its reason in the file; those
  values are unmeasured.
- When the harness itself ends a task (a limit, a provider failure, the end
  of the run), the answer carries a fixed `cause` such as
  `token_budget_exhausted_before_request`. `hacklab failures` groups by it.
- A failed request is retried with a growing pause (`http_retries`); a
  malformed reply gets one repair turn (`max_retries`). The first costs time,
  the second tokens.
- Nothing passes from one task to the next: no answers, no summaries.
- The replay cache and the trace (`HACKLAB_TRACE_DIR`) hold model replies and
  task data. Both live in the ignored `.state/` directory and are off in the
  submission image.

## Adapt during the day

1. Read a failing task's record and trace before changing anything
   ([playbook](playbook.md#read-a-run)).
2. A task type code can answer: a playbook. One the model gets wrong for a
   nameable reason: a skill, or a line in `arena-rules.md`.
3. A platform tool whose result the model misreads: shape the result in
   `arena_tools`, not in the prompt.
4. Measure one change at a time on a replayed seed and on a fresh run, and
   write it into `JOURNAL.md`.
