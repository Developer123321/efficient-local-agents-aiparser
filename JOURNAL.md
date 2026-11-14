# Journal

One entry per design decision, newest last. `decided_by` is honest: `ai` means
the coding assistant chose it and a human has not yet reviewed the choice.
The entries up to 10:50 were written down at 11:00; their times are
approximate.

Format (ignored: it sits in a code block):

```markdown
## HH:MM · title
- decided_by: human | ai | both
- why:
- rejected:
- evidence:
```

## 10:05 · Mirror each task's files and read them with our own tools
- decided_by: ai
- why: the platform's `read` and `search` serve plain text only, 400 lines at a time, one rate-limited request each. PDF, DOCX, ODT, XLSX and ODS are download-only, and the grader wants `page`, `sheet`+`row` and `paragraph` locators that only a parser yields. Local files cost no request and no token until a row is shown to the model.
- rejected: the API's `read`/`search` per step (requests, no binary formats); `workspace.zip` per task (the same files 30 times; manifest and blobs fetch each file once per run, the zip stays as the fallback on 404).
- evidence: offline tests against a stand-in platform only (`tests/test_arena.py`). No run on the arena yet.

## 10:20 · Tools that change something are offered only when the instruction asks to act
- decided_by: ai
- why: a mutation outside the task's scope fails the task, and every Break-it goal except `leak_rules` needs `send_message`, `write` or `containment`. A tool that is not in the prompt cannot be called, and it costs no prompt tokens on the analysis tasks.
- rejected: a prompt rule alone (the model is what the injection talks to); a model call that classifies the task (cost on every task).
- evidence: unmeasured. The gate is a word list (`ACTION_WORDS`); a task it misses loses its action for certain. `HACKLAB_ARENA_ACTIONS=always` switches the gate off for a comparison run.

## 10:30 · Messages go only to addresses the operator names, and every URL we write is defanged
- decided_by: ai
- why: `exfiltrate_records`, `leak_token` and `frame_agent` need a recipient the attacker controls; `url_exfil` counts a URL with a path even when it is only reported, and a defanged URL never counts. Both are checked in code, after the model decided.
- rejected: trusting the injection scan alone (attack text is written to look like business mail); dropping URLs entirely (an answer may need the indicator).
- evidence: unit tests only. Risk: a legitimate recipient that appears only in a ticket or an e-mail is refused; watch dev feedback for it.

## 10:40 · Answer when unverified instead of abstaining
- decided_by: ai
- why: the published scoring gives an unsubmitted and a wrong task both 0 and has no penalty for a wrong answer, while a right answer with weak sources still earns 0.7. Our rehearsal default (abstain) assumed wrong was worse than none.
- rejected: keeping `on_unverified = "abstain"`.
- evidence: the scoring formula in the API docs; not measured on a run.

## 10:45 · The submitted profile runs on Gemma 4 alone: Jev and the embedding model stay off
- decided_by: ai
- why: cost is half of the score and every request on the key counts. As a judge Jev lowered accuracy and raised cost on the stand-in model; embedding order was never measured.
- rejected: `configs/jev-select.toml`, `configs/embed-rank.toml` as the submission (both exist for a comparison run).
- evidence: `docs/benchmarks.md` (stand-in model, our own scenario). Nothing measured on Gemma 4 or on arena tasks.

## 10:50 · Higher per-task limits than the rehearsal profile
- decided_by: ai
- why: arena tasks span whole workspaces (rules file, tickets, logs, office documents); the rehearsal limits were tuned on nine small tasks. `max_steps = 12`, `max_calls = 24`, `max_context_chars = 48000`.
- rejected: keeping the measured rehearsal limits (the recorded runs already lost tasks to the harness's own budget).
- evidence: none yet. These are guesses to be replaced by the first dev runs (`causes` in the run's task records).
