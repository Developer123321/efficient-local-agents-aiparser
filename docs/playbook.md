# Plan for Saturday 14 November 2026

Times are the organizers'; the steps are our own plan for a team of up to
four. Rules and open questions: [hackathon.md](hackathon.md).

## Friday evening

- [ ] ID or passport that matches IBM's invitation is packed.
- [ ] AIM Slack works on the laptop you bring.
- [ ] On that laptop: `git pull`, then `just ready`, `just check`,
      `just practice` and `just container-test` all pass.
- [ ] No Docker or Podman? Then you depend on the limited test submissions.
- [ ] Your coding assistant is logged in. If it has a five-hour usage window,
      send it a short message at 07:00 so a fresh window opens around 12:00.
- [ ] Charger, and phone tethering as a second way online.

## Roles

One owner per area keeps four people out of each other's files.

| Role | Does | Files |
| --- | --- | --- |
| Integrator | Starter kit, task and answer format, container, submissions | `cli.py`, `Dockerfile`, `examples/kit_adapter.py` |
| Analyst | Reads the data by hand, writes playbooks | a new playbook module, `forensics/` |
| Prompt and loop | Prompt, skills, tools, guards, model settings | `agent.py`, `configs/*.toml`, the skills directory |
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
   until it is green.
3. Connect kit and harness with the least glue: either the kit calls
   `hacklab.agent.run_task` with its tools wrapped
   ([examples/kit_adapter.py](../examples/kit_adapter.py)), or
   `hacklab submit` reads the kit's task files.
4. Submit the unmodified or minimally adapted example once. This tests the
   container path while a failure is still cheap.
5. Set `[budget]` to the team's allowance and
   `export HACKLAB_CACHE_MODE=readwrite` in every shell.

## 10:45 Learn the task types

- **Analyst:** solve three tasks by hand with `rg`, `jq`, `hacklab decode`
  and `hacklab iocs`. Write down the task types and what decides each answer.
- **Measurer:** turn the organizers' tasks and feedback into `bench/*.jsonl`
  cases. Put a third aside as a hold-out file that nobody tunes against.
- **Prompt and loop:** run `configs/baseline.toml` and `configs/live.toml` on
  the same cases; `hacklab failures` shows where points are lost.
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
   solver ([template](../examples/event_playbooks.py)). It costs no tokens and
   cannot be talked out of its answer.
2. **A skill per task type that code cannot solve.** Five lines with the real
   file and column names and one example of the right tool calls
   ([examples](../examples/skills)).
3. **Fewer calls.** Raise `preload_results`, set `preload_documents_under`,
   lower `max_steps`.
4. **Smaller prompts.** Remove unused tools from `[agent] tools`, lower
   `max_observation_chars`.
5. **Model settings.** Thinking low or off through `extra_body`.
6. **Guards and abstention.** `on_unverified`, `injection`, `allowed_actions`,
   decided by the scoring rules.
7. **Jev**, only if it is reachable in the final run and the benchmark shows
   a gain after its cost.

Rules for the afternoon:

- A change without a before and after number is not kept.
- Read the trace before changing a prompt
  (`HACKLAB_TRACE_DIR=.state/trace`).
- Never tune on the hold-out file.
- Run `hacklab budget` before every live benchmark. Keep a fifth of the
  allowance for the last hour.
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

- Every task gets a valid answer, also after a tool error or a timeout.
  `run_task` guarantees this; keep new code inside it.
- It needs nothing but the endpoints the organizers provide. If Jev is
  missing there, the agent carries on without it.
- A playbook checks the files and columns it relies on and returns `None`
  when the data looks different, so the model takes over.
- It finishes in time: `submit --workers N` if parallel requests are allowed,
  `--deadline-seconds` so that late tasks still get an answer line.
