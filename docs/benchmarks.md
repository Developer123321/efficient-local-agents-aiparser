# Benchmarks and measurement

Three kinds of cases exist, and none of them is the organizers' benchmark:

- `bench/smoke.jsonl`: eight small document cases (lookup, approved versus
  draft, constraints, table arithmetic, hostile instructions, abstention).
- **The rehearsal incident** from `hacklab practice --seed N --out DIR`: logs
  of a model hub under attack by hijacked agents with nine tasks per seed
  (first compromised agent, scope of compromise, tokens to revoke, datasets to
  take down, a chunked and encoded payload, a count, an injected message, a
  full containment, a question the evidence cannot answer). Any seed gives a
  new incident with the same rules, so a seed you have not looked at is a
  hold-out set.
- **Your cases from the organizers' development tasks**, built on the day.

Each line is `{task, expected, category}`. Only `task` enters `run_task`;
expected answers and categories stay in the evaluator. The local score is 0 or
1: every declared check must pass.

| `expected` field | Passes when |
| --- | --- |
| `status` | The answer has this status (`ok` by default, `abstain`, `error`) |
| `exact_answer` | The answer equals it, ignoring case and spacing |
| `answer_set` | The answer lists exactly these items, in any order and punctuation |
| `contains` / `excludes` | Each text occurs / does not occur in the answer |
| `citation_documents` | Each document is cited at least once |
| `actions` | The containment actions are exactly these (type, target) pairs |

Quotes are always checked against the files. For `actions`, the record also
carries `action_precision` and `action_recall`: a missed step and a wrong
target both fail the case, and they are different problems. Text matching is a
transparent regression heuristic; extend the rubric to the organizers' rules
once they are known.

## Reading a run

```sh
uv run hacklab benchmark --config configs/live.toml --dataset bench/dev.jsonl \
  --root bench/dev --out results/live.json
uv run hacklab failures results/live.json
```

`failures` groups the lost cases by category and failed check. As a first
diagnosis: `required_documents` failing points at retrieval or a missing
tool; an answer check failing while the sources are right points at the model
or at a rule that belongs in a playbook; a `status` mismatch points at the
abstention policy, a guard or a provider error; `actions` failing with high
recall and low precision means over-containment. `calls_per_task` shows where
turns are spent.

## Comparing two configurations

```sh
uv run hacklab benchmark --config configs/baseline.toml --dataset bench/dev.jsonl \
  --root bench/dev --repeat 3 --out results/baseline.json
uv run hacklab benchmark --config configs/candidate.toml --dataset bench/dev.jsonl \
  --root bench/dev --repeat 3 --out results/candidate.json
uv run hacklab compare results/baseline.json results/candidate.json
```

`compare` reports the change in accuracy, known cost, calls and input and
output tokens, and refuses files with different data, modes or task counts.
Tokens and calls stay comparable while prices are unknown. A configuration
dominates another only if accuracy does not fall, known cost does not rise and
one of them improves. There is no invented combined score: the organizers'
normalisation is not known.

For a causal result, copy a config and change exactly one setting. The shipped
profiles are starting points: `baseline` has no Jev, no preloaded evidence and
no inventory; `live` adds them; `jev-select` also lets Jev filter the
preloaded evidence; `practice` solves the rehearsal with playbooks and no
model. One quick run does not establish a better configuration; use
`--repeat` when the model samples.

## What a real small model did (13 November 2026)

Measured with `configs/openrouter.toml`: IBM Granite 4.2 8B through
OpenRouter, the model alone with the tools, no playbooks. It is a stand-in for
whatever the event provides. Costs use OpenRouter's list prices of that day
($0.06 and $0.15 per million tokens). One run per cell at the provider's
default sampling; treat differences of one task as noise.

| Run | Rehearsal incident, 9 tasks per seed | Calls | Input tokens | Cost |
| --- | --- | --- | --- | --- |
| First harness, reasoning off, seed 7 | 4 of 9 | 55 | 176,000 | $0.0109 |
| Final harness, reasoning off, seed 7 | 6 of 9 | 52 | 121,000 | $0.0077 |
| Final harness, reasoning off, seed 8 | 5 of 9 | 56 | 156,000 | $0.0100 |
| Final harness, reasoning low, seed 7 | 7 of 9 | 35 | 66,000 | $0.0050 |
| Final harness, reasoning low, seed 8 | 6 of 9 | 35 | 64,000 | $0.0045 |
| Final harness, reasoning low, seed 9 | 5 of 9 | 34 | 71,000 | $0.0049 |
| Same plus Jev as final judge, seed 7 | 6 of 9 | 44 | 78,000 | $0.0056 |
| Same plus Jev as final judge, seed 9 | 5 of 9 | 42 | 87,000 | $0.0062 |
| Playbooks, any seed | 9 of 9 | 0 | 0 | $0 |

The eight document fixtures: 8 of 8 in 10 calls for $0.0007.

What the traces showed, and what was changed because of it:

- The model read ranked search hits on a log as the complete answer. Search
  results from tables are no longer preloaded; the first prompt describes each
  table instead (frequent values per column, time range), and a search that
  names a column value points to the exact filter.
- It wrote filters in other dialects (`{"actor": {"$in": [...]}}`, lists,
  `agent-*`, `src_ip_outside`). These are now accepted.
- "What did the compromised agents do after their compromise" took it many
  turns and usually failed. The `after` tool answers it in one call, and the
  model uses it.
- It retyped long lists and dropped or invented items. Lists are now results
  with an ID (`r2`); the answer and the actions can use a result whole, a
  retyped preview is completed by code, and a result ID counts as evidence.
- It decoded single chunks of a payload. `decode` now reassembles the whole
  series when given one chunk or several evidence rows.
- With reasoning off it wandered until the step limit; with low reasoning it
  sometimes abstained before using any tool. A step countdown and one
  question back on an abstention without investigation address both.
- Low reasoning effort used fewer calls, cost less than half and was at least
  as accurate as reasoning off. High effort was not measured.
- Jev as judge of the finished answer was harmful when it saw only the quoted
  rows (2 of 9: it rejected correct answers). Given the tool results as
  state it rated correct answers 0.8 to 0.98 and most wrong ones below 0.7.
  A rejection now triggers one more attempt. The net effect was mixed: wrong
  answers became abstentions, and once a correct answer did too. Use it if a
  wrong answer costs more than an abstention.
- EmbeddingGemma changed nothing on these fixtures: short documents are shown
  in full, so search is not what decides them. It ranks paraphrases correctly
  in a direct test; it can only matter for long prose documents.

Still failing with the model alone: the full containment and the two-part
revocation rule. Both combine several filtered lists under a policy; the
playbooks solve them. That is the division of labour to expect on the day.

To repeat: put a key into `.env`, then `just practice-live 7
configs/openrouter.toml`. Set `HACKLAB_TRACE_DIR=.state/trace` to get the
conversation of every task as a private text file; that is how the findings
above were made.

## Spending the request budget

The development budget is limited, so measure with the replay cache on:

```sh
export HACKLAB_CACHE_MODE=readwrite
```

An unchanged request is then replayed instead of sent. After changing one tool
or one playbook, only the tasks whose prompts changed cost anything. A replay
keeps the usage it had when live, so the reported cost per task is what an
uncached run would cost; `hacklab budget` shows what was really sent. Each
repetition of `--repeat` has its own cache entries, because repetitions exist
to show variance. `HACKLAB_CACHE_MODE=replay` sends nothing and fails on a
miss: use it to re-score old runs or to work while the endpoint is down.

Changing the system prompt, the tool list or a model parameter changes every
request and therefore pays for the whole set again. Batch such changes, and
check `hacklab budget` first.

Do not let reference answers reach the cache: it stores replies to prompts,
and prompts never contain labels as long as only `task` enters `run_task`.

## Splits and the final choice

Keep development cases, a hold-out file and the organizers' feedback apart.
Tune on the development cases; run the hold-out once per candidate before the
freeze; choose by hold-out accuracy and cost. For playbooks, generate two or
three fresh rehearsal seeds, or hold back organizer tasks of the same type: a
solver that only fits the cases it was written against will fail on the
unseen final scenario.

Benchmarks run sequentially for clear request attribution. `hacklab submit
--workers N` runs tasks in parallel; use it only after the organizers have
said that parallel requests are allowed and what the rate limits are.

Mock mode exercises wiring without a key and abstains from tasks that need
inference. Its accuracy is not model accuracy. The playbooks' perfect score on
the rehearsal shows that the tools work on data generated for them, nothing
about the real scenario.
