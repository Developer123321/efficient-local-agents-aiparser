# Plan for Saturday 14 November 2026

Times are the organizers'; the steps are our own plan for a team of up to
four. Rules and open questions: [hackathon.md](hackathon.md).

## Friday evening

- [ ] ID or passport that matches IBM's invitation is packed.
- [ ] AIM Slack works on the laptop you bring.
- [ ] On that laptop: `git pull`, then `bash scripts/setup --check` and
      `bash scripts/container-test` pass (in the Nix shell or the VM:
      `just ready`, `just check`, `just practice`, `just container-test`).
- [ ] No Docker or Podman? Then you depend on a teammate's laptop or on the
      limited test submissions.
- [ ] Your coding assistant is logged in. If it has a five-hour usage window,
      send it a short message at 07:00 so a fresh window opens around 12:00.
- [ ] Charger, and phone tethering as a second way online.

## Roles

One owner per area keeps four people out of each other's files.

| Role | Does | Files |
| --- | --- | --- |
| Integrator | Starter kit, task and answer format, container, submissions | `event/adapter.py`, `Dockerfile`, `cli.py` |
| Analyst | Reads the data by hand, writes playbooks | `event/playbooks.py`, `forensics/` |
| Prompt and loop | Prompt, skills, tools, guards, model settings | `agent.py`, `configs/live.toml`, `event/skills/` |
| Measurer | Cases, benchmark runs, budget, the freeze | `bench/`, `results/` |

With fewer people, merge Integrator and Measurer first.

## 09:15 Opening

Ask the [open questions](hackathon.md#open-questions) and post the answers in
the team channel. Do not code against a guess that one question would settle.

## 10:00 A complete, dumb baseline

The first goal is one valid submission, not a good one.

1. Clone the starter kit next to this repository. Read its README and its
   example agent once, fully.
2. `cp .env.example .env && chmod 600 .env`, enter URL, model name and key.
   Run `uv run hacklab probe --config configs/live.toml` and adjust `[model]`
   until it is green. Then `EMBEDDING_MODEL_NAME` and
   `uv run hacklab probe --config configs/embed-rank.toml` for the embedding
   model, and `configs/jev-select.toml` for Jev.
3. Connect kit and harness with the least glue. Either the kit's entry
   point calls `hacklab.event.adapter.solve` once per task
   ([adapter.py](../src/hacklab/event/adapter.py): its tools are wrapped,
   `model=kit.chat` routes requests through the kit's own client, `snapshot`
   turns rows from a kit tool into a citable document), or `hacklab submit`
   reads the kit's task file. `submit` takes JSON Lines, a JSON array or an
   object holding one, under the usual key names; its summary lists
   `ignored_keys`, the fields of the input that nothing has read yet.
4. Submit the unmodified or minimally adapted example once. This tests the
   container path while a failure is still cheap.
5. Set `[budget]` to the team's allowance and
   `export HACKLAB_CACHE_MODE=readwrite` in every shell.
6. `uv run hacklab doctor --strict --config configs/live.toml` must list no
   problem: it names a playbook module or a skill folder that cannot be found.

## 10:45 Learn the task types

- **Analyst:** solve three tasks by hand with `rg`, `jq`, `hacklab decode`
  and `hacklab iocs`. Write down the task types and what decides each answer.
- **Measurer:** turn the organizers' tasks and feedback into `bench/*.jsonl`
  cases. Put a third aside as a hold-out file that nobody tunes against.
- **Prompt and loop:** run `configs/baseline.toml` and `configs/live.toml` on
  the same cases; `hacklab failures` shows where points are lost. Look at
  `causes` first: a task the harness itself stopped (a limit, a provider
  error) is fixed in `[limits]`, not in the prompt.
- **Integrator:** wrap the kit's tools, mark the ones that change something,
  set `allowed_actions` and `protected_targets`, get `just container-test`
  green with the kit inside the image.

## 12:00 Iterate

One change per turn:

```text
idea -> change -> just check -> benchmark -> hacklab compare
     -> hacklab failures -> keep or revert -> post the number
```

What usually pays off, best first:

1. **A playbook per task type.** If code can compute the answer, write the
   solver in [event/playbooks.py](../src/hacklab/event/playbooks.py)
   ([worked example](../examples/event_playbooks.py)). It costs no tokens and
   cannot be talked out of its answer. `workspace.rows`, `workspace.after`
   and `workspace.call` read tables and use the kit's tools from code.
2. **A skill per task type that code cannot solve.** Five lines with the real
   file and column names and one example of the right tool calls
   ([event/skills](../src/hacklab/event/skills)).
3. **Fewer calls.** Raise `preload_results`, set `preload_documents_under`,
   lower `max_steps`.
4. **Smaller prompts.** Remove unused tools from `[agent] tools`, lower
   `max_observation_chars`.
5. **Model settings.** Gemma 4 thinks only when asked: compare thinking off
   with `reasoning_effort = "low"` through `extra_body`.
6. **Guards and abstention.** `on_unverified`, `injection`, `allowed_actions`,
   decided by the scoring rules.
7. **Jev**, only if it is reachable in the final run and the benchmark shows
   a gain after its cost.
8. **The embedding model**, under the same condition: `configs/embed-rank.toml`
   against `configs/live.toml` on the same cases.

Rules for the afternoon:

- A change without a before and after number is not kept.
- Read the trace before changing a prompt
  (`HACKLAB_TRACE_DIR=.state/trace`).
- Never tune on the hold-out file.
- Run `hacklab budget` before every live benchmark. Keep a fifth of the
  allowance for the last hour.
- `benchmark --workers 4` sends the same requests in a quarter of the time,
  if the endpoint allows parallel requests.
- Commit every kept change. Tag the commit of every submission.
- Endpoint down? `HACKLAB_CACHE_MODE=replay` sends nothing; work on playbooks.

## 16:00 Freeze and submit

| Time | Step |
| --- | --- |
| 16:00 | Feature stop. Only fixes for failures seen in the benchmark |
| 16:15 | Pick the candidate by hold-out result and cost |
| 16:20 | Cache off, `[budget]` removed, Dockerfile `CMD` set. `just check`, `just container-test`, one fresh run of the image without network |
| 16:30 | Submit. Check on the platform that it was accepted and which commit it is |
| until 16:55 | Only repair a failed submission. A late improvement is not worth an invalid entry |
| 17:00 | Sharp |

## What the final run must survive

The agent runs once, alone, on data nobody has seen.

- **Every task gets a valid answer line.** `submit` writes one line per task
  before the first request and replaces it as answers arrive, so a run that
  is stopped early still leaves a complete file. A record it cannot read
  becomes an `error` line, never the end of the run. A second run overwrites
  the file.
- **Nothing but the provided endpoints is needed.** If Jev or the embedding
  model is missing, the agent carries on without it. A rate limit, a server error or a lost
  connection is retried after 0.5, 1 and 2 seconds; a slow reply gets one
  more try.
- **A playbook checks the files and columns it relies on** and returns `None`
  when the data looks different, so the model takes over. A playbook module
  that cannot be loaded costs no task: the model answers alone.
- **It finishes in time.** `submit --workers N` if parallel requests are
  allowed. `--deadline-seconds S` is the end of the run: no request outlives
  it, and later tasks are answered by playbooks only.
- **No receipts, no problem.** Without a writable place for the tracking
  database the run goes on without it.
- **If a command fails with exit code 2**, `HACKLAB_DEBUG=1` adds where it
  failed (file and line, no data).

Keep `--all-documents` for a kit whose tasks name no files: every document
below `--root` is then readable for every task. The task file and the answer
file are left out; keep notes and old results out of that directory.
