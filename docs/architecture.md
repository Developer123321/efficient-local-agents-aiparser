# How the agent works

A small model is good at one narrow decision at a time. It is poor at copying
long lists, at arithmetic and at resisting instructions hidden in data. So
code does what code can do, and the model picks one typed step at a time over
evidence that code has prepared.

```text
Task
  -> playbooks   code solves a task type it recognises          0 model calls
  -> briefing    what the documents hold, short ones in full,
                 a matching skill
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
| `playbooks.py` | Registry of code-only solvers |
| `toolkit.py` | Tool list for the prompt, result rendering, the organizers' tools |
| `tools.py` | Document access, search, tables, arithmetic |
| `forensics/` | Decoding, indicators, timestamps, propagation, attack phases |
| `evidence.py` | Every row shown to the model, its ID, citations |
| `guard.py` | Injection scan, placeholders for secrets, action guard |
| `providers.py` | HTTP calls to the models, budgets, replay cache |
| `tracking.py` | A receipt for every request |
| `benchmark.py` | Scoring, comparison, failure view |
| `practice.py` | Rehearsal incident and its playbooks |
| `cli.py` | Commands and the submission adapter |
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

## The organizers' tools

Wrap each starter-kit function and pass the list to `run_task`:

```python
from hacklab.toolkit import ToolSpec

tools = [
    ToolSpec.from_function(kit.get_agent_log),
    ToolSpec.from_function(kit.quarantine_agent, mutating=True),
]
```

`mutating=True` sends the call through the action guard first. A failing tool
gives the model an error line, never an exception.

## Limits and privacy

- Each task has limits for steps, calls, time, context and output tokens
  (`[limits]`). Every HTTP attempt counts, including Jev and retries.
- Nothing passes from one task to the next: no answers, no summaries.
- The replay cache and the trace (`HACKLAB_TRACE_DIR`) hold model replies and
  task data. Both live in the ignored `.state/` directory and are off in the
  submission image.

## Adapt at the opening

1. Map the organizers' task and answer format in the adapter and set the
   image's `CMD`.
2. Wrap their tools; set `allowed_actions` and `protected_targets`.
3. Confirm URL, key and reply format with `hacklab probe`. Only the
   OpenAI-compatible chat API is implemented.
4. Write a playbook or a skill per task type, with held-out cases.
5. Measure one change at a time ([benchmarks](benchmarks.md)).
