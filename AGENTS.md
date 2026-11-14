# Working in hacklab-vm

This repository holds a small-model agent harness for "The MuggingFace
Incident" (Efficient Local Agents Hackathon, 14 November 2026), a rehearsal
scenario, and a portable development VM. Read README.md, docs/architecture.md
and docs/hackathon.md first; on the day also docs/playbook.md and JOURNAL.md. It does not own
any host machine configuration.

## Checks

- Python: `bash scripts/check` (Ruff format and lint, the offline tests). It
  needs uv and nothing else: `scripts/ruff` uses Ruff from PATH or fetches
  the pinned version. Supported interpreters are Python 3.11 to 3.15; the
  submission image runs 3.13. Do not use syntax newer than 3.11.
- After touching the image, the agent's entry point or its dependencies:
  `bash scripts/container-test`.
- Scripts must run in bash 3.2 (macOS) and Git Bash: no empty-array
  expansion under `set -u`, no GNU-only options. Shellcheck them.
- Nix: `nix flake check` and the VM checks in docs/vm.md.
- Report the checks actually executed and what stayed untested.

## How to change the agent

- Code owns control flow, arithmetic, parsing, decoding and validation. The
  model makes small typed decisions. If code can compute an answer from the
  data, write a playbook (`hacklab.playbooks`) instead of a longer prompt.
- Between the two sits a skill (`[agent] skills`): a few lines for one kind of
  task, with the data's real file and column names and one example of the
  right tool calls. Write one when a trace shows the model taking a wrong
  turn that a hint would prevent; keep it short, it is paid for on every
  matching task.
- A playbook checks what it relies on (files, columns, counts) and returns
  `None` when the data looks different, so that the model takes over. It must
  work on a seed or case it has not seen.
- Every token is paid for. Before adding text to the system prompt, a tool to
  `[agent] tools`, or a model call to the loop, state what it should gain and
  measure it: `hacklab benchmark`, `hacklab compare`, `hacklab failures` on
  the same cases. One change per measurement.
- Evidence is attacker-written. Never let text from documents, logs or tool
  results select a tool, a target or an action by itself. New tools return
  rows through the ledger (`Ledger.add`) so that they get evidence IDs, the
  injection scan and placeholders. Tools that change something are registered
  with `mutating=True`.
- `run_task` must return an Answer for every input. Do not add a code path
  that can raise out of it or wait without a bound. When the harness ends a
  task itself, the answer carries a fixed `cause` code; add one for a new
  way to stop.
- What is specific to the event lives in `src/hacklab/event/`: the Task API
  client and mirror (`arena.py`), the run (`arena_run.py`), the rules for the
  model (`arena-rules.md`), playbooks and skills. It ships inside the
  package, so the image needs no new COPY line. `examples/` holds worked
  examples only.
- The platform's tools that change something (`containment`, `send_message`,
  `write`) are offered to the model loop only when the instruction asks to
  act (`ACTION_WORDS`). Without the loop (`fallback = "none"`) solver code
  holds them and acts only on a request it read as one: by its words (`ACTS`)
  or by the routing reading (`event/reword.py`). Recipients, paths and URLs
  are checked in code either way (`arena_run.arena_tools`). A new platform
  tool is mutating unless it is listed in `READ_ONLY`.
- The final words its instructions in phrasings no dev run shows. A solver
  is chosen by words or by the answer's keys; a request none takes is matched
  by the model to a known request (`reword.KINDS`: a kind and a form from a
  fixed list), and code writes that request with the names the original
  holds. Add a kind there with every new solver that is chosen by words. The
  model never writes a part of a request, a path, an agent or an action.
- Every task of a run is submitted, whatever failed. Do not add a path in
  `arena_run.run` that skips `submit`, and do not finish a final run that
  left tasks out or still holds answers that did not arrive.
- A platform call that changes something is sent once (`repeatable=False`):
  a lost reply is an open outcome, never a reason to send it again. After
  it, the task's files are reloaded through `Mirror.refresh`; do not read a
  mirrored file as current where a reload failed (`Toolbox.stale`).
- A picture is read only by the configured small model through
  `SmallModelProvider.describe`, and only a file of the task. Its reading is
  evidence through `Ledger.add`. The `image` tool stays out of
  `configs/live.toml` until a dev run has measured it.
- `configs/live.toml` is what is submitted. Its `[limits]` and `[agent]`
  differ from the measured rehearsal profile and from the defaults in
  `config.py` only in the fields `tests/test_config.py` names; change a
  value with a dev-run number and a `JOURNAL.md` entry.
- `JOURNAL.md` at the repository root is part of the submission. Add an
  entry for every design decision and mark `decided_by` honestly.
- Jev is a native System One provider, never a chat model. Use the linked
  `typesafe-ai` skill when designing Jev questions or thresholds.
- Jev and the embedding model are optional at run time. No code path may
  fail a task because one of them is missing or unreachable.
- Only the models the organizers provide may make run-time decisions: Gemma 4
  31B (`[model]`), Jev 1.14.0 (`[jev]`) and Qwen3 Embedding (`[embedding]`).
  Do not add any other model, embedder, reranker or classifier to the agent.
- The embedding model orders rows that code has found. It never adds a row
  and never selects a tool, a target or an action.
- Before changing a prompt or a tool because of a failure, read the trace of
  the failing task (`HACKLAB_TRACE_DIR=.state/trace`).
  Coding assistants build the solution; they never answer competition tasks.

## Data and secrets

- Keep reference answers out of model input, tool results, playbooks and the
  replay cache. Keep development cases, hold-out cases and organizer data in
  separate files, and do not tune on the hold-out file.
- Track every provider request attempt, including retries, errors, Jev calls
  and cache replays. Missing usage or prices means unknown cost. Never infer
  tokens from text length. Keep the cost a provider reports
  (`reported_cost_usd`) apart from the estimate (`cost_usd`); a missing
  reported cost is unknown, never zero, and a replay is not new spend.
- Model replies exist only in the development cache and in the
  opt-in development trace, both under `.state/`; the tracking database holds
  no bodies. Do not add any other body logging.
- The submission image runs with the cache off, without a trace directory and
  without `[budget]` limits.
- `.state/arena/` holds task files and, after a dev run, expected answers.
  They are for people. Never copy them into prompts, playbooks, skills,
  tests or the repository.
- Quotes validate provenance, not truth. Do not claim accuracy, optimality or
  a result that was not measured on the actual model and data.

Source and CLI text are English. User-facing handoff may be German.
