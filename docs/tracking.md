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
input/output tokens and configured prices, and, where the endpoint reports
them, cached prompt tokens and hidden reasoning tokens. Jev and the small
generative model are accounted separately. Mock calls are explicitly
nonbillable and remain distinguishable from live calls.

A reply served from the development cache gets its own record with status
`cache_hit` and the usage the request had when it was live. Summaries
therefore show what a run costs without a cache (`calls`, tokens, `cost_usd`)
and how much of it was replayed (`cache_hits`). `hacklab budget` and
`Tracker.spend()` count only requests that were really sent, across the whole
database, and compare them with `[budget]`. They cover this database only:
requests from a teammate's laptop are not in it, so split the team's allowance
between laptops or ask the organizers' platform for the total.

A run that a playbook answered has no call records at all and an event
`playbook`.

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
