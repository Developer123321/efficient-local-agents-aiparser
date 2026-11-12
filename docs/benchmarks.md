# Benchmarks and optimization

`bench/smoke.jsonl` contains eight synthetic development cases covering lookup,
approved-vs-draft conflict, supplier constraints, table arithmetic, hostile
document instructions and justified abstention. These are deliberately visible
practice fixtures. They are neither an independent held-out evaluation nor the
organizer benchmark.

Each line is `{task, expected, category}`. Only `task` enters `run_task`; expected
answers and category labels are kept in the evaluator. The local score is 0 or 1:
all declared status, text, excluded-text, required-document and quote checks must
pass. Text matching is only a transparent regression heuristic; a passing fixture
can still miss semantic errors. Extend the rubric for the revealed scenario,
especially numeric tolerances, hard constraints and authoritative-source rules.

Mock mode exercises wiring without an API key. It intentionally abstains from
tasks requiring inference. Its accuracy is not model accuracy and should not be
optimized. Unit and HTTP-contract tests exercise successful planning/tool/final
paths and retry/validation behavior using synthetic responses.

## Controlled live comparison

Start from a run with Jev and preloading disabled, then add them:

```sh
uv run --locked hacklab benchmark --config configs/baseline.toml \
  --dataset bench/smoke.jsonl --repeat 3 --out results/baseline.json
uv run --locked hacklab benchmark --config configs/live.toml \
  --dataset bench/smoke.jsonl --repeat 3 --out results/candidate.json
uv run --locked hacklab compare results/baseline.json results/candidate.json
```

`compare` reports the change in accuracy, known cost, calls and input/output
tokens. Tokens and calls remain comparable while the event's prices are
unknown. `configs/jev-select.toml` is `live.toml` plus Jev's passage filter.

These example profiles vary multiple settings and are starting points. For a
causal ablation copy the baseline config and change exactly one setting. Use
the same provider model, price schedule, dataset, document bytes and repeat
count. Randomize/alternate order when rate limits or server load vary. Inspect
per-case failures, total actual attempts, tokens, estimated cost and p95 time.
One quick run is not enough to establish a better configuration.

The report binds fixture labels and document bytes to one SHA-256 digest and
stores a separate config digest. The comparison command rejects differing data,
mock/live modes, task counts or benchmark types. A configuration Pareto-dominates
another only if accuracy does not fall, known cost does not rise and at least
one improves. Unknown cost remains unknown. There is no invented combined score:
the event's exact accuracy/cost normalization has not been published here.

## Data split and final submission

Keep separate development, validation and final holdout datasets. Do not inspect
holdout labels while iterating; do not let a coding assistant copy labels into
prompts, retrieval indexes or a response cache. Use small, fixed validation sets
for fast iteration and the held-out set once to choose the final configuration.

The harness disables cross-task memory and response caching. It runs benchmarks
sequentially for clear request attribution and to avoid accidental bursts. Add
bounded concurrency only after measuring the organizer's rate limits and ensuring
the final evaluator permits it. If the organizer supplies a benchmarking client,
wrap that client's task adapter and store its official output separately.

Recommended order of improvements: fix deterministic parsing/rules; improve
retrieval and conflict evidence; reduce unnecessary model turns; then test Jev
gates and thresholds. More agents and more verification calls need measured
benefits because cost counts alongside correctness.
