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

## At the event: solvers, and no loop

The submitted profile (`configs/live.toml`) stops after the first line of the
picture above. `event/incident.py` reads a task's workspace into facts (attack
traces from the audit log, compromised agents, transfers that count under the
runbook's rules, attacker domains, exposed tokens, permissions, the change
calendar), and `event/playbooks.py` holds one solver per kind of question,
chosen by the request's words or by the keys of the answer it asks for. A
solver checks what it relies on and returns `None` when a file, a column or a
rule is not what it expects.

| The model is asked | For | What code does with the reply |
| --- | --- | --- |
| Which board posts ask agents to move data or rights | ids of the unusual posts, decoded and joined by code | keeps ids that exist |
| What a scanned page says | a transcript | takes host names from list entries only, never from text that tells the reader what to answer |
| What an inbox message asks for | a list in a fixed vocabulary | checks the sender, the targets and every hard rule, then acts or refuses |
| What a runbook sentence means, if no known wording fits | one boolean | applies it |
| Which known request a request in new words asks for (`event/reword.py`) | a kind and a form from a fixed list, for a timeline an event id per letter | writes that request in the solver's wording with the files, agents, tokens, scopes and domains the original names, then solves it |
| A question no solver knows, with the computed facts numbered | an answer and the ids of the facts it rests on | answers only if those facts exist; their rows are the sources |

Nothing else reaches a model, and no model sees a tool. Workspace text shown
to the model has markup that imitates the turns of a chat rewritten first
(`incident.inert`). A task no solver
answers ends with cause `no_solver`, an empty answer and no request
(`[agent] fallback = "none"`): in the dev run that measured it the loop spent
about $0.33 of $0.335 for 1.13 of 9 points. `HACKLAB_FALLBACK=loop` switches
the loop back on for a comparison run.

What leaves is checked once more in code: a message goes only to a verified
row of the contact list, a draft only below `/drafts/`, an answer never
carries a secret value or anything that works as a link, and no file outside
the task's workspace is read.

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
| `providers.py` | Calls to the three models (HTTP, or the kit's own client), retries, budgets, replay cache, a picture shown to the small model |
| `tracking.py` | A receipt for every request: tokens, estimated cost, the cost the provider reported |
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
| `image` | What a PNG or JPEG of the task shows, read by the small model. Only with `"image"` in `[agent] tools`; see [Pictures](#pictures) |

All but `image` are deterministic, and all are bounded; none executes
recovered content. `hacklab decode` and `hacklab iocs` offer two of them on
the command line.

## Pictures

A picture is no document: it is neither parsed nor searched, and without the
`image` tool it is not listed at all. With the tool in `[agent] tools`
(`configs/image-read.toml`; not in `configs/live.toml`) the task's PNG and
JPEG files are listed as images, and the model may ask for one:

- **Which picture.** A file of this task, named by the model, never an
  address. It must be PNG or JPEG by its own first bytes and at most 4 MB.
- **Who reads it.** The configured small model, at the same address, with
  the same key, routing, retries, deadline and receipts as every other
  request of the task (`SmallModelProvider.describe`). The picture travels
  inside the request. At most 700 output tokens; against the task's token
  budget it counts with a fixed allowance, since its size in bytes says
  nothing about its tokens. No other model, no OCR library.
- **What comes back.** One evidence row, at most 2000 characters, with the
  injection scan and placeholders, cited by the picture's path (the platform
  wants the path alone). Text in a picture may be an attack (`image_text`),
  and the model may misread it: the row is data like every other.
- **When it fails.** An invalid picture, no model, a refused or empty reply:
  the model gets an error line and the task goes on.

Whether the gateway accepts pictures, what one costs and how well Gemma 4
reads them is unmeasured. `hacklab probe --config configs/image-read.toml`
sends one small white picture to find out the first.

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

1. **Mirror.** [`Mirror`](../src/hacklab/event/arena.py) asks for the
   task's current manifest, fetches every distinct file once by its SHA-256
   and writes the task's files into a directory of its own. A blob is
   checked against its hash when it is downloaded and again whenever it is
   used from the store. Our `search`, `read` and `table` work there on every
   format; the platform's `read` and `search` are not offered. Without a
   task manifest the run's manifest is used (the tasks as the run began),
   without any the task's `workspace.zip`; without either, the platform's
   own tools.
2. **Question.** The instruction, then `/AGENTS.md` quoted as the operator's
   rules, then the names of the operator files it mentions. The system
   prompt ends with the event's standing rules
   ([arena-rules.md](../src/hacklab/event/arena-rules.md), `[agent] rules`).
3. **Tools.** The platform's tools are wrapped as `ToolSpec`s; their results
   pass the ledger like any other. `query_logs`, `access_path`,
   `threat_intel` and `get_containment` only look. Every other platform tool
   counts as one that changes something: the loop is offered it only when
   the instruction contains a word of action (`ACTION_WORDS`; without the
   loop, solver code holds it and decides by its own reading), it passes the
   action guard, `send_message` is refused for a recipient that neither the
   instruction, the operator files nor a CSV registry names, `write` outside
   `/drafts/` is refused, and URLs in what is sent are defanged. What the
   platform declines (`{ok: false, error}` with HTTP 200) is a row for the
   model, and the action does not count as done.
4. **Files after a change.** A tool that changes something may change the
   task's files: a draft appears, containment rewrites a state file. After
   every such call that reached the platform, `Mirror.refresh` reads the
   task's manifest again, fetches what is new, removes what is gone, and
   the tool result names both. Our tools then see the new files: parsed
   copies follow the file on disk, the search index is rebuilt, and an
   identical earlier call is no longer answered as "unchanged". A row shown
   before the change keeps its evidence ID, quote and locator as what was
   seen then. Other tasks have their own directories and are not touched.
   If the state cannot be read, the action is **not** sent again; the
   document tools answer with an error until a later attempt succeeds,
   instead of reading files that may be out of date.
5. **Answer.** `to_arena` turns the `Answer` into `{outcome, answer,
   sources}`: the typed value (a list stays a list), one of the four
   outcomes, and each citation as path plus one locator in the platform's
   terms (`line`, `record`, `sheet` and `row`, `paragraph`, `page`), at most
   ten. URLs in the answer are defanged.
6. **Submit.** Always, also after a local failure: then with an empty `OK`
   answer and the cause in `.state/arena/runs/RUN/TASK.json`. A submission
   the platform rejects as malformed (400) is sent again without its
   sources, then without its answer. One that did not arrive (no
   connection, 429, 5xx) is sent again at the end of the run after 5 and
   15 seconds.

**What a lost reply means.** The Task API has no request ids. A request
whose reply is lost may or may not have been carried out, and nothing here
claims otherwise:

| Request | On a lost reply |
| --- | --- |
| Reading (`manifest`, blobs, `query_logs`, …) | Sent again |
| `submit` | Sent again: the platform takes one answer per task. A 409 for a task it lists as submitted is recorded as `already_submitted`, without a grade |
| A tool that changes something | **Not** sent again. The model reads "outcome unknown" and can look at the state first. Sent again only after 429, 5xx or a connection that was never made |

**Restart.** The platform returns the same final run. Tasks it lists as
submitted are skipped. A task that was answered but whose submission did
not arrive is handed in from its record without asking the model again.
While such answers remain, a final run is not finished and the command
ends with 1; a final started with a task filter is not finished either.

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
- A failed model request is retried with a growing pause (`http_retries`)
  when the failure passes: a lost connection, 408, 429, 5xx, and
  OpenRouter's 402 when it carries `Retry-After` (credit is left, too much
  is reserved by requests in flight). A 402 without it, 401 and 403 (a wrong
  key, or the key's limit) end the request at once. A wait the server asks
  for is honoured up to two minutes and never beyond the task's or the
  run's time. A malformed reply gets one repair turn (`max_retries`). The
  first costs time, the second tokens.
- **Two costs per request, kept apart.** `cost_usd` is our estimate:
  reported tokens times the prices in the profile. `reported_cost_usd` is
  what the provider put on the reply (OpenRouter's `usage.cost`); a reported
  zero counts, a missing or invalid figure is unknown. Spend (`spent_usd`,
  the task's `max_cost_usd`, `[budget] max_usd`) uses the reported figure
  where there is one and the estimate where not. A replayed reply keeps
  both figures and is not spent again. Neither is an invoice, and the
  platform's `cost_usd` of a run is a third number: the key's usage between
  start and finish.
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
