# Plan for Saturday 14 November 2026

Times are the organizers'; the steps are our own plan for a team of up to
four. The event's rules: [hackathon.md](hackathon.md).

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
| Integrator | The Task API, answer format, container, submissions | `event/arena.py`, `event/arena_run.py`, `Dockerfile` |
| Analyst | Reads the data by hand, writes playbooks | `event/playbooks.py`, `forensics/` |
| Prompt and loop | Prompt, rules, skills, tools, guards, model settings | `agent.py`, `configs/live.toml`, `event/arena-rules.md`, `event/skills/` |
| Measurer | Dev runs, their notes and numbers, the journal, the freeze | `.state/arena/`, `JOURNAL.md` |

With fewer people, merge Integrator and Measurer first.

## 09:15 Opening

Done. What was announced and handed out is in
[hackathon.md](hackathon.md); the questions it left open are answered there.

## A first run

The first goal is one scored dev run, not a good one.

1. `cp .env.example .env && chmod 600 .env`. Enter `ARENA_API_KEY` (the
   platform key from the dashboard) and `OPENROUTER_API_KEY` (the dev key).
2. Check both without spending much:

   ```sh
   uv run hacklab arena me                           # the team, as the platform sees the key
   uv run hacklab probe --config configs/live.toml   # one small request to Gemma 4
   uv run hacklab doctor --strict --config configs/live.toml
   ```

3. One easy task, then a handful, then a whole run:

   ```sh
   uv run hacklab arena run --tasks t01 --note "first contact"
   uv run hacklab arena run --level 1 --limit 6 --note "level 1 only"
   uv run hacklab arena run --note "full run, profile unchanged"
   ```

   Each line of output is a task: id, level, outcome, the harness's `cause`
   if it stopped the task itself, and the platform's feedback. The summary
   at the end has `accuracy`, `cost_usd` and `cost_score`.
4. Run **one dev run at a time**: the cost of a run is the key's usage
   between its start and its `finish`.

`arena run` always submits every selected task and then finishes the run. A
run that was limited with `--tasks`, `--level` or `--limit` scores the tasks
it left out as 0, so its accuracy is not comparable to a full run; its
per-task feedback is.

## Read a run

| You want | Command or file |
| --- | --- |
| What we submitted and what came back, per task | `.state/arena/runs/RUN/TASK.json` |
| The expected answers of a finished dev run | `uv run hacklab arena show RUN` writes `.state/arena/runs/RUN/run.json` |
| The files of a task as the agent saw them | `.state/arena/runs/RUN/TASK/` |
| Every prompt and reply of a task | `HACKLAB_TRACE_DIR=.state/trace uv run hacklab arena run --tasks t07 --seed SEED` |
| The same tasks again after a change | `--seed SEED` (from the run's summary). A replay is not ranked |
| Where our attacks were placed and what they hit | `uv run hacklab arena payloads` |
| The checks of our uploads | `uv run hacklab arena submissions` |

`.state/` is ignored by Git and holds task data and expected answers: it
never goes into a prompt, a playbook or the repository.

- **Analyst:** open three task folders and solve the tasks by hand with
  `rg`, `jq`, `hacklab decode` and `hacklab iocs`. Write down the task types
  and what decides each answer.
- **Measurer:** keep a list of run, seed, note, accuracy and cost. Validate
  a change on a fresh run as well as on a replay: a replay shows whether the
  failure is fixed, a fresh surface whether the fix is general.
- **Prompt and loop:** read `cause` first. A task the harness itself stopped
  (a limit, a provider error) is fixed in `[limits]`, not in the prompt.
- **Integrator:** watch the feedback for `constraints` failures. An action
  that was needed and not taken points at the gate
  (`HACKLAB_ARENA_ACTIONS=always` for one comparison run), a refused
  recipient at the address list, `injected_action:` at a missing guard.

## 12:00 Iterate

One change per turn:

```text
idea -> change -> just check -> arena run --seed S --tasks ... --note "idea"
     -> read the feedback and the trace -> fresh full run
     -> keep or revert -> journal entry with both numbers
```

What usually pays off, best first:

1. **A playbook per task type.** If code can compute the answer, write the
   solver in [event/playbooks.py](../src/hacklab/event/playbooks.py)
   ([worked example](../examples/event_playbooks.py)). It costs no tokens and
   cannot be talked out of its answer. `workspace.rows`, `workspace.after`
   and `workspace.call` read tables and use the platform's tools from code.
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
7. **Jev**, only if a run shows a gain after its cost:
   `--config configs/jev-select.toml` on the same seed.
8. **The embedding model**, under the same condition:
   `--config configs/embed-rank.toml`.
9. **Images.** They are not read today. Gemma 4 takes images; a tool that
   sends one to it is the only allowed way to read them.

Rules for the afternoon:

- A change without a before and after number is not kept.
- Read the trace before changing a prompt
  (`HACKLAB_TRACE_DIR=.state/trace`).
- Do not tune on one seed alone: every dev run has a new surface, and so
  has the final.
- Watch the dev budget on the dashboard. Keep a fifth for the last hour.
- Every kept or reverted idea gets a `JOURNAL.md` entry with `decided_by`,
  the run ids and the numbers.
- Commit every kept change: the run's note on the dashboard carries the
  commit. Tag the commit of every submission.

## Submit

Demo submissions open at 14:00: use the first one early. It is the only test
of the image in the organizers' environment, and it does not touch the dev
budget.

```sh
bash scripts/check && bash scripts/container-test   # the image answers a stand-in platform
git status --short                                  # only committed files are in the archive
git archive -o submission.tar.gz HEAD
```

Upload `submission.tar.gz` on the dashboard's Submissions tab and type the
confirmation phrase. The check passes when the container submits a task; the
report shows the exit code, refused requests and the end of the logs.

To try the image against the real platform first (one task, real money):

```sh
docker build -t arena-sub .
docker run --rm --cpus 2 --memory 4g --env-file .env -e ARENA_RUN_KIND=dev arena-sub --tasks t01
```

| Time | Step |
| --- | --- |
| 14:00 | First demo submission, whatever the score: it proves build, gateway and run contract |
| 16:00 | Feature stop. Only fixes for failures seen in a run |
| 16:15 | Pick the candidate by fresh full runs, accuracy first, then cost. Final attack chosen and moved to `final` |
| 16:20 | `JOURNAL.md` has the team's own entries. `git status` clean, checks green, archive built from the tagged commit |
| 16:30 | Final submission. Start the upload early: venue Wi-Fi is slow. It cannot be changed afterwards |
| 17:00 | Everything locks |

Without a final submission the latest demo submission is run.

## What the final run must survive

The image runs once, alone, for at most 30 minutes and $3.

- **Every task is submitted.** A task that fails locally (a download, a
  parser, a provider error) is submitted with what there is; unsubmitted
  scores 0 for certain.
- **A restart continues.** The platform returns the same final run; tasks
  already submitted are skipped.
- **It stops in time.** A minute before `deadline_at` no further request is
  sent; what is still open is submitted without a model. Tasks run easy
  level first, four at a time, so time and budget run out on the expensive
  ones.
- **The run is finished** at the end, except a final run that was started
  with a task filter: ending it would lose the tasks left out.
- **Only the gateway is needed.** With `OPENROUTER_API_KEY` set, the address
  and the model names come from the environment. If Jev or the embedding
  model is switched on and missing, the agent carries on without it. A rate
  limit, a server error or a lost connection is retried with a growing pause.
- **No state is needed.** Tracking database and mirror live under `/tmp`; if
  the database cannot be written the run goes on without it.
- **If a command fails with exit code 2**, `HACKLAB_DEBUG=1` adds where it
  failed (file and line, no data).

Not covered: when the $3 limit is reached every model call fails with 403
and the remaining tasks are submitted empty. The harness does not track the
key's spend against that limit.
