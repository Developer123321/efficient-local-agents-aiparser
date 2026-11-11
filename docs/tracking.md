# Local experiment tracking

The harness writes SQLite receipts to a dedicated private state directory. The
directory has mode `0700`; the database and JSON exports have mode `0600`. Keep
that directory outside Git and outside Nix inputs. SQLite WAL supports several
local processes without a separate tracking server or external account.

Each run records its task ID, mode, start/end, output status, optional benchmark
score, configuration digest, selected non-secret configuration, and supplied
version/source digests. The final answer is represented by a SHA-256 digest.
Prompt bodies, document text, tool arguments, source quotes, answer text,
authorization headers and arbitrary error messages are not recorded. Metadata
uses an allowlist, and provider URLs retain only their origin.

Every actual model request, including retries and failed attempts, needs its own
`record_call`. The record includes provider/model, latency, status, reported
input/output tokens and configured prices. Jev and the small generative model
are accounted separately. Mock calls are explicitly nonbillable and remain
distinguishable from live calls.

`cost_usd` is an estimate from reported usage and configured prices, never an
invoice. Without both token counts and both prices, the call cost is `null`.
An HTTP failure can still have incurred a charge; missing usage stays unknown.
An aggregate containing any unknown call therefore has `cost_usd: null` and
`unknown_cost_calls > 0`. `known_cost_usd` reports only the measured subtotal.
Token totals are also `null` when incomplete; `known_input_tokens` and
`known_output_tokens` expose the available subtotals. Output tokens priced at
zero still need an explicitly configured zero rate.

Set event prices from the organizers before comparing cost. Public list prices
can differ. Do not convert output byte counts, character counts, elapsed time,
or mock runs into provider-token measurements. Keep cold starts, repeated
trials, configuration changes and benchmark splits visible when comparing runs.

Use `Tracker.export(path)` for a private JSON snapshot of runs, calls, events
and summary. Export does not upload or publish anything. Preserve separate
organizer evaluation results: the local practice score is not the official
accuracy/cost ranking.
