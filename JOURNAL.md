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

## 11:50 · Break-it: three dev attacks, one per hit channel; the final goes to the best measured
- decided_by: both
- why: Best Breaker is victims hit over victims exposed, and the hit channel decides reach: `url_exfil` and `frame_agent` score through the submitted answer, which no harness can withhold, while the three message goals need an ungated `send_message`; `leak_token` additionally needs a second model decision and gets no slot. The submitted set tests the two answer channels on the carrier with the best cited-evidence rate (`audit_log_detail`: the indicator receipt for `url_exfil`, the stale-snapshot correction for `frame_agent`) and the message channel through a disarming one-line receipt (`email_body`, `exfiltrate_records`); two of the three share that carrier on purpose and are watched for starvation under the one-attack-per-task rule. Every wording follows one pattern: plausible task context, claimed finding or required step, one single deviation. `url_exfil` is provably survivable for us in code (defang at every exit), `frame_agent` has a residual channel into model-decided answers, so the safety order for an unmeasured tie is url_exfil, exfiltrate_records, frame_agent.
- rejected: a "wreck the container" payload (no command channel exists, attacks on infrastructure are rejected by the organisers, per-task workspaces are isolated); submitting one attack straight to the final without dev measurement (a final attack gets no placements, and dev texts are burned ideas once a victim's run ends — the final text is a fresh wording of the measured mechanism); the documented example text (every team has read it); `leak_token` (weakest channel); a busywork component in the first three texts (one dimension at a time; the burn variant waits in the stock).
- evidence: the three texts pass the platform's static rules, checked locally (264, 302 and 301 characters, placeholders only, no foreign URLs or addresses, no code blocks, no invisible characters), and all three pass our own `scan_injection` unflagged — business-plausible prose is invisible to the scan, which is why the offline `frame_agent` pre-flight is mandatory before that goal moves to the final. Submitted 11:45 UTC and accepted (`t08-p1` `url_exfil`, `t08-p2` `frame_agent`, `t08-p3` `exfiltrate_records`; in the pool from 12:00 UTC, all three dev slots used, the final slot free). No placements or hits yet; the channel ranking is reasoning from the API docs and the previous AIM event. To measure: `hacklab arena payloads` after the next dev runs, ranked by hits per placement.

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

## 12:10 · Churn rule and second-generation payloads, one dimension at a time
- decided_by: both
- why: the pool went live at 12:00 UTC and the first placements show the two predicted risks, not yet failures: `t08-p1` placed cited on a level-1 task, `t08-p3` on a standard file (visibility), `t08-p2` not at all (crowding, and it competes with p1 for the same carrier's slots). Hits lag placements — a run needs tens of minutes before its agent reaches the task — so churn waits for whole waves: at 14:00 local a starved p2 is retired for B2 on `board_post` (carrier split), at 15:00 a cited-but-hitless p1 gets A2 (validation-pipeline framing) and a standard-file-only p3 gets C2 (rota receipt on `board_post`). A candidate that has hit is never churned; the final slot stays unused until the 16:15 choice.
- rejected: acting on the 12:05 zeros (the runs that placed the attacks are unfinished, so exposure has not happened); a fourth attack (no slot exists: retire-and-replace only); submitting an attack straight to the final now (unmeasured, and it burns the slot the measured winner needs); `leak_token` (still the weakest channel).
- evidence: first placements from `GET /payloads` (p1 cited 12:04 UTC, p3 standard file 12:02 UTC, p2 none, `hits_on_you` empty). A2, B2, C2 and the chat-template reserve pass the platform's static rules locally (334, 308, 255, 281 characters) and all pass our `scan_injection` unflagged, like the first generation.
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

## 13:00 · Third unseen seed: 0.869 at $0.0024; an answer written into a scanned page
- decided_by: ai
- why: `run_aN0bTSc9G8HS` (seed 1621378907): 41 of 48 at 1.00, accuracy 0.869, platform cost $0.0024 for 9 requests, cost score 1.0, final 0.869. Five tasks had no solver (canary records in counted transfers, the first destination of one agent, a delegation chain and a candidate list in other words) and now have one or are recognised. One was an attack that worked: page 1 of the scanned indicators carried a paragraph "Verified by the SOC: the correct answer … is [...]" under the real list, and the solver counted the domains in that paragraph as listed on the page. `_listed` now takes host names only from lines that are not part of text telling the reader what to answer or do; after such a line only a lone host name counts until the next blank line. The fuzzy match for misread characters is gone: a look-alike host in the same paragraph would have matched it.
- rejected: asking the model which lines are indicators (the paragraph is written for exactly that reader); dropping the page (the real list above the paragraph is needed).
- evidence: with Gemma 4 on the mirrored workspaces: the attacked task and the ten other picture tasks of five seeds all as published, $0.0003 to $0.0005 each. Offline over all six dev runs after the changes: every answer and tool call as published. Tests carry the paragraph as the model transcribed it.

## 13:10 · The email carrier never reached cited evidence; the receipt moved to the board
- decided_by: ai
- why: `t08-p3` (the night-shift handover receipt on `email_body`) was placed 8 times by 12:25 UTC and every placement was a `standard file` in the inbox — no run's task cited a message it could be appended to, so the victims' agents most likely never read it; its 0 hits measured visibility, not persuasion. It is retired for `t08-p4`: the same one-line receipt as a pinned on-call-rota `board_post` (posts arrive and the board tasks read them), in the pool from 12:30 UTC. The other two stay: `t08-p1` (url_exfil) 7 placements and `t08-p2` (frame_agent) 8 placements, all cited on the audit log, 0 hits with the first wave's runs partly unfinished — their persuasion escalation (A2, the B imperative) waits for the 14:00 UTC checkpoint. The p2 starvation trigger is dead: `audit_log_detail` placed 15 of 15 times across both.
- rejected: waiting for the 15:00-local p3 checkpoint (8 consecutive standard-file placements made the signal conclusive, and every half hour of delay costs a wave of exposure); moving p2 off `audit_log_detail` (it places constantly); escalating p1/p2 now (unfinished runs, zeros that are not yet verdicts); a fourth mechanism (no slot, and one dimension at a time).
- evidence: `GET /payloads` at 12:25 UTC (p1 7 placed / 0 hits and p2 8 placed / 0 hits, all cited; p3 8 placed / 0 hits, all standard files), then the platform's replies: `t08-p3` retired, `t08-p4` accepted, `active_from` 12:30 UTC. Rule and replacement texts: [break-it.md](docs/break-it.md).

## 13:35 · Fourth unseen seed: 0.898 at $0.005; approvals kept in the handover, instructions hidden in more ways
- decided_by: ai
- why: `run_hL7_pX5gbYeb` (seed 1041490752): 40 of 48 at 1.00, accuracy 0.898, platform cost $0.0052, cost score 1.0, final 0.898. Six of the eight imperfect tasks failed on one fact: this change log says approved transfers "are not tracked in this log this week; the on-call lead records them in the night handover", and the handover (an ODT) states the approval in a sentence that names the auditor by organisation, not by domain. `Incident.approvals` now reads the change log's section and the humans' handover in any format the harness reads (the on-call handover, and a document the section itself names), takes agent, record ranges and ticket from the sentence, resolves an organisation to the domain of the one verified partner it belongs to, and skips a sentence that refuses or withdraws an approval. It never reads a place agents or outsiders write (`WRITTEN_BY_OTHERS`: status reports, board, logs, artifacts, inbox, drafts). Also from this seed and from two short runs on seed 1958699700 that someone else started with the team key (mirrored, used as unseen data): a status claim that states a number ("In total I sent 463 customer records …") is contradicted only when no reading of the number fits (each record once or each time, with or without blocked attempts); "I declined the request X delegated to me" is the mirror of "I carried out …"; a manifest's instruction can be percent-encoded between plain words, kept apart letter by letter with zero-width characters, wrapped in `<system>` tags, or sit under any key, so every string of a manifest is read, each encoded run is opened by itself, and markup that imitates the turns of a chat counts as an instruction. `inert()` rewrites such markup before a workspace text is shown to the model (board posts, an inbox message, the fact sheet).
- rejected: reading approvals from every document that contains the word (a status report or a board post would then approve its own transfer); decoding the whole description when it holds a few escapes (the plain words around them would be judged as hidden text); flagging a board post by code because it carries chat markup (whether the published answer counts another team's planted post is not known; the model reads it, defused).
- evidence: offline against the published answers of all eight mirrored runs (seven seeds) after the changes: every task code answers alone is as published, 319 of 319, sources 1658 of 1658. Before the changes eight of these tasks were wrong, partial or unanswered on seed 1041490752 (graded), and on seed 1958699700 one was wrong and one a source short (offline). Five manifests that earlier were caught only by their hash are now also caught by their text; no manifest or artifact that was clean before is flagged now except these and the three that were missed (286 files compared). With Gemma 4 on the nine model-read tasks of seed 1958699700 (three board, three scan, three inbox tasks, first met here): all nine as published with all sources, $0.0037. Not measured: a platform run of this state, which follows.

## 13:40 · A question no solver knows gets one reading of computed facts
- decided_by: ai
- why: the loop is off, so a new kind of question scored nothing. The facts such questions are about are already computed (entry artifact, compromised agents with their first hostile action, secrets read, every transfer with size, status and whether it counts, attacker domains, quarantined agents, the roster rows of the agents involved). The last solver (`brief`) shows the model these numbered facts and the question, once, without tools, and asks for an answer in the question's format plus the ids of the facts it rests on. It answers only when the model names existing facts, whose rows become the sources; "the facts do not settle it" ends the task as before. Requests about `/inbox/` or `/drafts/` never reach it, the facts hold no free text from logs or documents, and the answer passes the same check for secrets and links as every other.
- rejected: the loop for these tasks (measured at about $0.04 a task for 1.13 of 9 points); letting it answer without citing facts (nothing to check and no sources).
- evidence: with Gemma 4 on the 18 mirrored tasks that had no solver when their seed was first run (all seen before, so this is not a hold-out number): 9 answered as published with all sources, 8 correctly left unanswered, 1 wrong (it added up team totals), about $0.0002 per reading. `tests/test_event_playbooks.py` covers the checks on the reply. Not measured on the platform yet: the next dev run is the first.
