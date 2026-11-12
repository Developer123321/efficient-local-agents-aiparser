# Day plan: Saturday 14 November 2026

Times follow the organizers' agenda; the steps are our own recommendations.
The plan assumes a team of up to four. Facts and open questions are in
[hackathon.md](hackathon.md).

## Friday evening

- [ ] IBM ID created under the invited mail address (deadline Friday 13
      November) if you want Bob credits. ID or passport matching IBM's
      invitation packed.
- [ ] AIM Slack joined on the laptop you bring.
- [ ] On that laptop: `git pull`, `just ready`, `just check`, `just practice`.
      All three must pass without a network.
- [ ] `just container-test` passes with your Docker or Podman. If you have
      neither, you depend on the limited platform submissions.
- [ ] Coding assistants logged in. If yours has a five-hour usage window, send
      it a short message at 07:00 so that a fresh window opens around 12:00.
- [ ] Charger, and a second way online (phone tethering) for the laptop.

## Roles

One owner per area keeps four people out of each other's files.

| Role | Owns | Files |
| --- | --- | --- |
| Integrator | Starter kit, task and answer mapping, container, submissions | `cli.py`, `Dockerfile`, the kit |
| Analyst | Reads the data by hand, writes playbooks and rules | a new playbook module, `forensics/` |
| Prompt and loop | Prompt, tool list, guards, model settings | `agent.py`, `configs/*.toml` |
| Measurer | Fixtures, benchmark runs, failure view, budget, the freeze | `bench/`, `results/` |

With fewer people, merge Integrator with Measurer first.

## 09:15 to 10:00: opening

Write the answers to the eight questions in
[hackathon.md](hackathon.md#questions-for-the-opening) into the team channel.
Do not start coding against a guess that one question would settle.

## 10:00 to 10:45: a complete, dumb baseline

The first goal is one valid submission, not a good one.

1. Clone the starter kit next to this repository. Read its README and its
   example agent once, fully.
2. `cp .env.example .env; chmod 600 .env`, enter URL, model name and key.
   `uv run hacklab probe --config configs/live.toml`. Fix the adapter settings
   in `[model]` until the probe is green; note the token usage it reports.
3. Decide the integration direction. Either the kit calls
   `hacklab.agent.run_task` for each task with its tools wrapped as
   `ToolSpec`s, or `hacklab submit` reads the kit's task files. Prefer the
   one with less glue.
4. Run the kit's own example end to end and make one platform submission of
   the unmodified or minimally adapted agent. This tests the container path
   while a failure is still cheap.
5. Set `[budget]` in the config to the team's allowance and export
   `HACKLAB_CACHE_MODE=readwrite` in every development shell.

## 10:45 to 12:00: learn the task types

- Analyst: open the development data and solve three tasks by hand with
  `rg`, `jq`, `hacklab decode` and `hacklab iocs`. Write down the task types
  and what decides each answer.
- Measurer: turn the organizers' development tasks and feedback into
  `bench/*.jsonl` cases with expected answers. Keep a third of them aside as
  a hold-out file that nobody tunes against.
- Prompt and loop: run `configs/baseline.toml` and `configs/live.toml` on the
  same cases; `hacklab failures` shows where points are lost.
- Integrator: wrap the kit's tools, mark the mutating ones, set
  `allowed_actions` and `protected_targets`, get `just container-test` green
  with the kit inside the image.

## 12:00 to 16:00: iterate

One loop, repeated, with one change per turn:

```text
idea -> change -> just check -> benchmark (cache on) -> hacklab compare
     -> hacklab failures -> keep or revert -> note the result in the channel
```

Levers, in the order they usually pay off:

1. **A playbook per task type.** If code can compute the answer from the
   data, write the solver and register it. It costs no tokens and is immune
   to injected text. Check it on the hold-out file.
2. **Fewer calls.** Raise `preload_results`, set `preload_documents_under`,
   lower `max_steps`. Look at calls per task in `hacklab failures`.
3. **Smaller prompts.** Remove unused tools from `[agent] tools`; lower
   `max_context_chars`; shorten the system prompt only against measurements.
4. **Model settings.** Thinking off or low through `extra_body`, the
   documented sampling parameters, `response_format = "json_schema"` if the
   endpoint enforces schemas.
5. **Guards.** `injection = "withhold"`, `on_unverified`, stricter
   `allowed_actions`. Decide by the scoring rules from the opening.
6. **Jev**, if it is reachable in the final run: as a filter for preloaded
   evidence, as the final judgment, or as a `choose` router in a playbook.
   Keep it only if the benchmark shows a gain after its cost.

Rules that protect the afternoon:

- A change without a before and after number is not kept.
- Never tune on the hold-out file; run it before the freeze, once per
  candidate.
- `hacklab budget` before every live benchmark. Keep a fifth of the allowance
  for the last hour.
- Commit every kept change. Tag the commit of every platform submission.
- If the model endpoint is down or slow, switch to
  `HACKLAB_CACHE_MODE=replay` and work on playbooks and tools; nothing is
  sent in that mode.

## 16:00 to 17:00: freeze and submit

- **16:00** Feature stop. Only fixes for failures seen in the benchmark.
- **16:15** Candidate chosen by hold-out result and cost. Cache off
  (`[cache] mode = "off"`, no `HACKLAB_CACHE_MODE` in the image), `[budget]`
  limits removed from the submitted config so that the final run cannot stop
  on the development allowance.
- **16:20** `just check`, `just container-test`, one fresh run of the
  candidate image on the development tasks without network.
- **16:30** Submit. Verify on the platform that the submission was accepted
  and which commit it is.
- **16:30 to 16:55** Only if a submission fails: fix that, resubmit. A late
  improvement is not worth an invalid final entry.
- **17:00** is sharp.

## What the final run must survive

The agent runs once, alone, on a scenario nobody has seen.

- Every task returns a valid answer, also after a tool error, a timeout or a
  malformed document. `run_task` is built that way; keep new code inside it.
- No request needs the internet beyond the endpoint the organizers provide.
  `just container-test` checks that the image works with no network at all.
- Playbooks must not assume file names, column names or counts from the
  development data without checking them; a solver that meets something
  unexpected returns `None` and the model takes over.
- The run finishes within the time limit: `submit --workers N` for parallel
  tasks if allowed, `--deadline-seconds` so that late tasks still get a line.
- Nothing reads or writes outside the mounted task data and the output path.
