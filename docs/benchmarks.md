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
