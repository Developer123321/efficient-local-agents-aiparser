# Hackathon facts and preparation plan

Research checked on **11 November 2026**. The organizer's page was fetched
directly and its published schedule and FAQ checked. Event details can change;
confirm the current instructions at the opening.

## What the organizer publishes

The [AIM event page](https://ai-mission.eu/hackathons/2026-11-efficient-local-agents/)
lists the Efficient Local Agents Hackathon for **14 November 2026 at IBM Vienna**.
It allows teams of up to four people and welcomes coding assistants during
development. The scenario and small language model are announced at the opening.
At competition runtime, only that supplied model and **Jev 1.14 by TypeSafe AI**
may be used. The page describes a live development benchmark and an autonomous
final run on an unseen scenario.

The published weighting is **50% accuracy and 50% cost**, with correct sources
included in accuracy. No exact cost normalization or combined scoring formula
was verified. This repository therefore reports its own accuracy and usage
measurements separately; it does not reproduce the official score.

**Timing discrepancy:** the user's application text says final evaluation is at
18:00. The current official event page instead says **17:00 final evaluation**
and **18:00 leaderboard, presentations and awards**, subject to change. Prepare
against the earlier 17:00 deadline until the organizers confirm otherwise.

The actual small-model API, final input/output contract, organizer benchmark
endpoint, event prices and exact scoring procedure remain unverified. The local
JSONL adapter and OpenAI-compatible generative adapter are preparation choices,
not claims about the organizer's interface. API keys are expected from the
organizers on the day, according to the supplied application text.

## Verified model and development-tool interfaces

Jev uses a native decision API, not chat completions. The
[official System One reference](https://docs.typesafe.ai/api/system-one.md)
specifies `POST https://api.typesafe.ai/v1/systemone`, Bearer authentication and
a JSON body with `model`, `state` and a named `questions` map. Answers are keyed
by those question names. Responses include `model` and input/output token usage.
`Noul` answers contain a probability named `noul`; `Choice` answers include the
selected choice, probabilities and confidence. Keep this adapter separate from
the generative-model adapter.

The [official model reference](https://docs.typesafe.ai/models) identifies
`jev-1.14.0`, a default 64k-token request context and 32k-token per-question
context. It lists a public price of $0.042 per million input tokens with free
output tokens. **Those are public list prices, not verified hackathon billing.**
Use organizer prices for comparisons and leave unknown prices visibly unknown.
Pin the model ID because aliases can move. Record the returned model ID too.

The [TypeSafe introduction](https://docs.typesafe.ai/introduction) recommends
narrow, atomic decisions and composing their results in code. Independent
questions can share one state in one request. The
[confidence documentation](https://docs.typesafe.ai/confidence) explains that
Choice confidence is derived from its probability distribution; it is not an
independent second check. Noul has no separate confidence field. Thresholds
need calibration on representative examples.

[IBM Bob Shell](https://bob.ibm.com/docs/shell) is a coding and shell assistant.
The [official installation guide](https://bob.ibm.com/docs/shell/getting-started/install-and-setup)
currently requires Node.js 24 or later, documents the `@ibm/bob` package, and
supports IBMid/SSO or `BOB_API_KEY` authentication. Use the existing `bobshell.nix`
integration to build and inspect the project. Bob access does not replace the
two competition models or authorize a third runtime model.

For future integration changes, consult the
[official TypeSafe agent-skill instructions](https://docs.typesafe.ai/agent-skill),
the [skill source](https://github.com/typesafe-ai/skills/tree/main/skills/typesafe-ai)
and the [OpenAPI specification](https://api.typesafe.ai/openapi.json). These are
reference sources; the runtime need not download skills or documentation.

## Our recommended team plan

The steps below are engineering recommendations, not organizer requirements.

1. **Before the event:** boot or enter the environment on each actual laptop/VM;
   complete one locked dependency sync; run the documented offline checks and
   mock benchmark; verify team Git access and the local tracking database.
   Authenticate development assistants in advance. Bring a charged laptop and
   keep a known-good checkout available.
2. **At the opening:** confirm the deadline, budget, cost accounting, allowed
   tools, network access, API details and submission contract. Assign owners for
   API integration, document/data tools, benchmark analysis and final execution.
   Configure the supplied endpoint and model; make a minimal real request before
   adding strategy complexity. Run the simplest complete baseline immediately.
3. **Build representative fixtures:** translate revealed task types into small
   development examples. Hold back a separate validation set containing conflicts,
   missing evidence and constraint failures. Keep answer keys out of model input,
   tools and memory. Keep organizer data separate from synthetic fixtures and
   follow its reuse rules. Mock success verifies plumbing, not model quality.
4. **Make cheap operations deterministic:** use code for parsing, arithmetic,
   filtering, date normalization and constraint checks. Retrieve short passages
   with stable source locations. Preserve contradictory evidence rather than
   silently picking a source. Resolve it using the scenario's authority and
   recency rules; an exact quote proves provenance, not the claim's truth.
5. **Measure Jev's contribution:** compare the same fixtures with and without
   Jev verification or routing. Batch independent checks sharing the same state.
   Keep only changes that improve the measured accuracy/cost tradeoff. Track
   every attempt, including retries, failures and Jev calls; missing usage is
   unknown, never zero. Report latency and failure rate alongside accuracy.
6. **Freeze early:** our recommendation is a tested candidate and backup by
   16:30 if 17:00 remains the final deadline. Run the held-out set, inspect source
   handling and budgets, then pin the commit, configuration and dependency lock.
   Test one fresh-process submission against the confirmed contract. Ensure final
   execution can finish without a person answering questions or a coding
   assistant repairing it mid-run.

This setup provides a reproducible starting point. It does not establish that
the harness is optimal or predict a winning result before testing the actual
models, scenario and official evaluation.
