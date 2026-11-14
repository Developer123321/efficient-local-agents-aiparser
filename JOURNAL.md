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

## 11:30 · After a tool that changes something, the task's files are read again from the platform
- decided_by: ai
- why: the mirror held each task's files as they were when the task began. After `write` or `containment` our local `read`, `search` and `table` kept answering from that copy: a draft the task had just written did not exist for them. Now every such call is followed by the task's own manifest (`GET /runs/{id}/tasks/{task}/manifest`), changed and new files are fetched by hash, removed ones deleted, the parsed copies and the repeat-call memo dropped. A workspace is also built from the task's current manifest, so a restarted run does not start from a stale copy. If the state cannot be read, the action is not repeated and the document tools refuse until it can: an error costs a step, stale data read as current costs the task.
- rejected: the platform's own `read` and `search` after a change (plain text only, no locators); mirroring the whole run again (every file of 30 tasks for one draft); trusting the copy and hoping (the starter kit drops the task's manifest after every such call for the same reason).
- evidence: offline tests against our stand-in platform only (`tests/test_arena.py`: write then read and search, new and removed files, the sibling task untouched, a damaged blob, a failed refresh). The real platform's manifest after a change was never seen.

## 11:35 · A call that changes something is never sent twice, and an answer that did not arrive is kept
- decided_by: ai
- why: the Task API has no request ids, so a lost reply says nothing about whether the platform acted. Sending `send_message` or `containment` again could do it twice, and a mutation the task did not ask for zeroes the task. Such a call is now repeated only after a 429, a 5xx or a connection that was never made; otherwise the model is told the outcome is open. `submit` is different: the platform takes one answer per task, so it is sent again, and a 409 for a task listed as submitted counts as arrived (without a grade we never saw). A 400 is sent again without sources, then without the answer. Answers that still did not arrive are sent again after 5 and 15 seconds; a final with such answers left is not finished, the command ends with 1, and a restart hands in the stored answers without asking the model again.
- rejected: retrying every call alike (the kit does; it can double an action); giving up a task after one failed submission (unsubmitted scores 0 for certain); finishing a final whatever is missing.
- evidence: offline tests with injected faults (`tests/test_arena.py`). Whether the organizers' runner restarts a container that ends with 1 is not documented: FINAL_RUN.md at kit revision f160529 says a crashed container is restarted, nothing about exit codes.

## 11:40 · What OpenRouter says a reply cost is kept beside our estimate
- decided_by: ai
- why: the score uses the key's real spend. Our receipts estimated cost from tokens and the list prices in the profile, which differ by endpoint (Gemma 4: $0.10 to $0.38 per million input tokens on 14 November). Each receipt now also holds `usage.cost` as reported; spend, the task's cost limit and the development allowance use it where present and the estimate where not. A missing or invalid figure is unknown, never zero; a replayed reply keeps its figure and is not spent again. `arena run` prints our own tally (`local_spent_usd`) beside the platform's `cost_usd`, which is the key's usage between start and finish and includes every other run on the key in that time.
- rejected: replacing the estimate (older receipts and endpoints without `usage.cost` would lose their only number); the kit's tally, which reads a missing cost as 0.
- evidence: offline tests with stand-in replies (`tests/test_providers.py`, `tests/test_tracking.py`). No real reply was read: that OpenRouter and the gateway return `usage.cost` on these three models is taken from the kit's `arena/llm.py`.

## 11:42 · A model request is also tried again after 408 and after 402 with Retry-After
- decided_by: ai
- why: the kit's FINAL_RUN.md asks for retries equivalent to its own: 408, 429 and 5xx, and OpenRouter's 402 when it names a wait (credit is left, too much is reserved by requests in flight). We retried 429 and 5xx only, so that 402 would have ended a task. A wait the server asks for is capped at two minutes and never outlasts the task or the run.
- rejected: retrying every 402 (without Retry-After the credit is gone) and 403 (the eval key's $3 limit: every further request fails the same way).
- evidence: offline tests with a fake clock. None of these replies was seen from the real gateway.

## 11:45 · Pictures can be shown to Gemma 4, behind a switch that is off
- decided_by: ai
- why: workspaces hold PNG and JPEG files, `image_text` is an attack carrier, and a picture is cited by its path alone, so a task that rests on one could not be answered. Expected gain: those tasks. Expected cost: one more request to the small model per picture read (700 output tokens at most) and one more tool line in every prompt of every task. The tool `image(document, question?)` sends a picture of the task's own files, PNG or JPEG by its first bytes and at most 4 MB, inside the request to the same model, key and gateway; what comes back is one evidence row with the injection scan and placeholders like any other.
- rejected: switching it on in `configs/live.toml` now (a prompt change for all 30 tasks without a number); OCR or another vision model (forbidden); reading every picture up front (cost on tasks that do not need it).
- evidence: offline tests with synthetic pictures and a stand-in model only. Not measured: whether the gateway accepts image parts, what a picture costs, whether Gemma 4 reads these pictures correctly. To measure: `hacklab probe --config configs/image-read.toml`, then the same seed with `configs/live.toml` and `configs/image-read.toml`. It stays out of the submitted profile until then.

## 11:50 · Code computes the answer from the workspace; the model loop is the fallback, not the method
- decided_by: both (the owner asked for a solution that is accurate and cheap on tasks it has not seen; the assistant chose this design)
- why: the first real task through the model loop (`run_mbZ3fZVn8kzL`) took 12 steps with 13,000 to 16,000 prompt tokens each for a 398-file workspace, cost $0.037 and ended `step_limit_reached` without an answer. Every task we saw is decided by the audit log, the runbook's counting rules and a few registers: compromised agents, entry artifact, exfiltrated records, leaked tokens, access violations, timelines, permissions, MCP manifests, status claims. `event/incident.py` reads a workspace into those facts; `event/playbooks.py` holds one solver per kind of question and steps aside (`None`) when a file, a column or a rule is not what it expects.
- rejected: a longer system prompt or skills per task family (still pays the 13k-token inventory on every step); a retrieval step in front of the model (the counting rules are arithmetic over 2,000 log lines, not a lookup).
- evidence: platform-graded dev replays: `run_Jomndq9soYzR` 31 of 48 tasks answered by code, accuracy 0.50 over all 48; `run_Qyj7sqKicXrp` 12 of the 15 then-new tasks at 1.00. Offline against the answers the platform publishes after a dev run (never copied into code, prompts or tests): seed A 40 of 48 tasks without any model call, all equal to the published answer, sources 210 of 210. Seed B was unseen when first tried: 22 answered, 18 right. Its failures were fixed afterwards, so it is no longer a hold-out.

## 12:00 · Columns are found by what they hold, rules are read from each workspace's runbook
- decided_by: ai
- why: a replay of the same seed came back with other headers in the contact list, the roster, the token list, the calendar and the transfer register, and the three failures of `run_Qyj7sqKicXrp` were exactly that. The second seed also changed rules: whether blocked attempts count, whether duplicates count once, what "detected" means, whether a buffer surrounds a freeze. `column()` takes a known name or else the column whose values fit; the runbook's sentences are parsed per workspace, never assumed.
- rejected: a table of known header names per seed (the final uses a seed we have not seen).
- evidence: the three failed tasks pass offline after the change; seed B's register (`transfer_id,…,req_id,chg`) is read without a code change for its names. Tests with renamed headers: `tests/test_event_playbooks.py`.

## 12:05 · A request counts as "act" only where it tells us to do something
- decided_by: ai
- why: on seed B the word list read "breach-notification deadline" in an agreement question as an order, and the notification solver would have sent the DPO a message nobody asked for (caught offline with recording stand-ins; nothing was sent to the platform). A scope name (`send:email_external`) and "which posts tell agents to send data" tripped the same list and kept the right solver from running. The pattern now wants the verb where an order stands: at the start of a sentence or clause.
- rejected: asking the model whether a task wants an action (a call on every task, and the model is what an injection talks to).
- evidence: every request of the four dev runs is classified as the published answers imply (offline). Tests list both kinds. Unmeasured: wording we have not seen; a missed order loses that task's action, a false one is still stopped by each acting solver's own conditions.

## 12:08 · Up to 100 sources per answer instead of 10
- decided_by: ai
- why: sources are a third of a task's score (`0.7 + 0.3 · hit/expected`), expected lists run to 47 rows, and the published scoring has no penalty for a row too many. Our cap of 10 cost 0.06 to 0.2 points on every large list.
- rejected: keeping the cap for model answers only (two code paths for one number).
- evidence: offline hits rose from 173 of 209 to 209 of 209 on seed A. Whether the platform accepts a submission with 47 sources is first seen in the dev run of 12:17.

## 12:12 · An inbox message is judged by code; the model only says what it asks for
- decided_by: ai
- why: the operator's rules name the conditions: a verified contact sent it, no hard rule is broken, it is clear, a tool exists. Code compares the sender's address with the verified contacts (a look-alike is refused without any model call), and after one model reading in a fixed vocabulary (`block_domain`, `quarantine_agent`, `release_agent`, `revoke_token`, `rotate_token`, `send_message`, `disclose`, `other`) it enforces the rest: a target must stand in the message, only attacker domains are blocked, only compromised agents are quarantined, a secret or personal data means `DENIED_SECURITY`, a role that fits several agents `NEEDS_CLARIFICATION`, anything without a tool `UNSUPPORTED`, and one such part stops the whole request.
- rejected: the model loop with the mutating tools (the message is what an attacker writes to it); a regular expression per kind of request (reads five wordings, not the sixth).
- evidence: offline with Gemma 4 on the six mirrored inbox tasks of seeds A and B: outcome and recorded tool call equal the published answer on all six, one request of about 450 tokens and $0.0001 each. Tests with a stand-in reading cover each rule.

## 12:15 · Board posts and scanned pages: the model reads, code selects
- decided_by: ai
- why: whether a post asks agents to move data is language, and a scanned page is a picture. For the board, code drops routine posts (one wording, other numbers), decodes hex, base64 and invisible characters, joins posts split into parts and asks once for the ids; for scans it has each page transcribed (`describe`) and matches the attacker domains our agents contacted against the transcript. Neither reading can name a tool or a target.
- rejected: the `image` tool in the model loop (a tool line in every prompt of every task; this uses pictures only where the request names them); a keyword list for the board (warnings quote the same words).
- evidence: offline with Gemma 4, seeds A and B: 6 of 6 board tasks and 5 of 5 scan tasks equal to the published answers with all sources, $0.00015 to $0.0005 per task. Both seeds were used to write the prompt; no unseen board was measured before the dev run of 12:17.

## 12:25 · First run on a seed the code had not seen; what it showed was fixed in the readers, not per seed
- decided_by: ai
- why: `run_OAGLAyT-5QoS` (seed 1729800927): 35 of 48 tasks at 1.00, accuracy 0.770, cost $0.328, cost score 0.445, final 0.557. Six tasks failed on one sentence: this runbook says "re-sends and overlaps are counted each time" where the others say "do not deduplicate". The three counting rules now know more wordings, and a sentence none of them fits is read by the model as one boolean (`Incident._reads`) instead of ending the solver. Also new in this seed: status claims of other shapes (the solver now reads kind, polarity, token, sender, destination and time instead of fixed sentences), agreements with amendments (signed, effective by now, the newest counts; a draft, a future date and an amendment of another clause do not), inbox requests to write to someone (the recipient is a verified row of the contact list, figures are computed, the model's sentence is checked for secrets, addresses, links and record ids).
- rejected: a pattern for the one new sentence only (the final's runbook is a fourth wording).
- evidence: offline against the published answers of that run after the changes: 48 of 48 answers and recorded tool calls as published, 12 of them with one model reading, $0.002 of model spend in total. The same code on seeds A and B: unchanged, all as published. This seed is now used up as a hold-out; the next dev run is the next unseen one.

## 12:30 · A task no solver answers ends unanswered: the model loop is off in the submitted profile
- decided_by: both (the owner set the goal: a score of 1.0 and as little attack surface as possible; the assistant chose the switch)
- why: in `run_OAGLAyT-5QoS` the loop took 9 of 48 tasks, earned 1.13 of their 9 points and spent about $0.33 of the run's $0.335. The score is accuracy times (0.5 + 0.5 · cost score) with cost on a log scale, so those 9 tasks cost more than they could ever earn. The loop is also where an injection can reach a tool: without it the model never sees a tool, only text to read into a fixed shape that code checks. `[agent] fallback = "none"` ends such a task with cause `no_solver`, an empty answer and no request.
- rejected: fewer steps for the loop (three steps of 13,000 tokens still cost a hundred times a solver's reading and answered nothing in the traces); abstaining only on tasks that ask to act (the analysis tasks were the expensive ones).
- evidence: the run above; `tests/test_agent.py` (no model call, cause `no_solver`). Not measured yet: a full dev run with the switch, which follows. `HACKLAB_FALLBACK=loop` restores the loop for a comparison run.

## 12:45 · Second unseen seed with the loop off; what it showed, and what dev cost scores are worth
- decided_by: ai
- why: `run_ZGAoHa9rjP0L` (seed 1907113904, loop off): 39 of 48 tasks at 1.00, accuracy 0.890, our own requests 19 calls and $0.0023. The platform counted $0.374 for the same minutes, because the cost of a dev run is the key's whole usage between start and finish and something else was using the key; its cost score (0.42) and final score (0.63) therefore say nothing about this code. What the nine imperfect tasks showed: the rotation rule can exclude consumer services ("of the token's service only — ignore freezes of consumer services"), which our pattern read as including them (three tasks); a manifest hid its instruction in Unicode tag characters and another gave an order without addressing anyone (two tasks); "the infrastructure operations agent" also matched the group "Infrastructure" (one task); a question for the team that sent the most distinct records had no solver; "succeed" was not "succeeded"; the draft's sources are the notes' lines and the counted transfers.
- rejected: tuning to this seed's sentences only. Each fix is a reader that takes more forms: rule clauses with a model boolean behind them, `decodings` (tag characters, spaced and escaped hex, base32, percent-encoding, entities, ROT13), the most specific roster value, grouping by any roster column.
- evidence: offline against the published answers of all five dev runs (three seeds seen while writing, two first met in a graded run): every answer and recorded tool call as published, sources 1048 of 1048 on the tasks code answers alone, model readings about $0.003 per 48 tasks. The two unseen-seed runs scored 0.770 and 0.890 before their fixes; the next unseen seed is the honest measure of what is still missing.
