# Rules and open questions

Sources: the organizers' mail to participants (12 November 2026) and the
[event page](https://ai-mission.eu/hackathons/2026-11-efficient-local-agents/),
read again on 13 November. Details can change at the opening.

## The day

| | |
| --- | --- |
| When | Saturday 14 November 2026. Doors 09:00, opening and challenge reveal 09:15, lunch 12:00 |
| Where | IBM Vienna, Obere Donaustraße 95. Bring the ID that matches IBM's invitation |
| Deadline | **Code submission at 17:00, sharp** |
| After | The organizers run every agent once on an unseen scenario; leaderboard at 18:00 |
| Channel | Everything runs over the AIM Slack workspace |

## The task

"The MuggingFace Incident": a hijacked AI agent swarm attacks a model hub. Our
agent is the night shift. On a fixed small model it must investigate the
incident and contain it, correctly and cheaply. Details come at the opening.

- Every team gets the same small language model and the same budget, plus
  **Jev 1.14 by TypeSafe AI** for typed decisions ("supported or not?").
- **Score: 50 % accuracy (right answers and right sources), 50 % cost.**
- There is a live benchmark all day with feedback per task.
- Each team has a limited development budget for model requests.

## Allowed and forbidden

| Allowed | Forbidden |
| --- | --- |
| Any AI to **build** the agent (Claude Code, Codex, Bob, …) | Any own AI model **inside** the agent: LLM, embedder, reranker, classifier, online or offline |
| The two provided models at run time | Any other model at run time, also through a proxy, a tool or an MCP server |
| Any language, with our own Dockerfile | Internet access from the agent's container |
| Any amount of our own code: parsers, rules, tools | |

The starter kit and its tools are Python (3.11 or newer, uv). No coding
assistant is mandatory. IBM offers free Bob credits to those who created an
IBM ID under the invited mail address by Friday 13 November.

## "No internet" and the models

The mail says both of these:

> It will run in a container with no internet access.
>
> No GPU or own API keys needed – we hand out the model keys on the day.

Keys mean the models are reached over an API. So the container cannot be
fully cut off: the organizers must give it a route to the small model. How
that route looks, and whether it also reaches Jev, is not published.

What follows for us:

- **Jev is provided, not required.** Nothing obliges us to call it. As far as
  TypeSafe's public documentation shows, Jev exists only as a hosted API;
  there is nothing to install locally. If the final run has no route to Jev,
  Jev is simply not available there.
- **The agent works without Jev.** With `[jev] enabled = true` and no key or
  no route, it answers on the small model alone instead of failing. After
  three failed Jev requests in a row it stops asking for the rest of the run.
- **Both addresses come from the environment**: `SMALL_MODEL_BASE_URL` and
  `TYPESAFE_BASE_URL`. A gateway inside the container needs no code change.
- **Measure with and without Jev** and submit the variant that also works
  when Jev is missing, unless the organizers confirm the route.

`just container-test` proves all of this with a stand-in endpoint and no
network: both models reachable over a local address, and nine answers without
an error when Jev is absent.

## Open questions

Ask at the opening and write the answers into the last column. Until then the
middle column is what the repository assumes.

| # | Question | Our assumption until answered | Setting it changes | Answer |
| --- | --- | --- | --- | --- |
| 1 | How does the container reach the small model? Is Jev reachable there too? | A local address from the environment; Jev may be missing | `SMALL_MODEL_BASE_URL`, `TYPESAFE_BASE_URL`, `[jev] enabled` | |
| 2 | What is billed (input, output, hidden reasoning, cached tokens, requests) and at which price? Does a Jev call cost? | Every token counts | `[model]` prices, `extra_body`, prompt size | |
| 3 | How are accuracy and cost combined? Against what is cost normalised? | Unknown, so no combined score locally | which variant we submit | |
| 4 | Does a wrong answer or wrong action cost more than none? Is there partial credit? | Wrong is worse: abstain when unverified | `[agent] on_unverified`, `[guard]` | |
| 5 | What is the container's input and output, and the time limit of the final run? | JSONL in, JSONL out | `Dockerfile` `CMD`, `submit --workers`, `--deadline-seconds` | |
| 6 | Which containment actions exist? What must never be touched? | Any type, nothing protected | `[guard] allowed_actions`, `protected_targets` | |
| 7 | How large is the development budget? How many test submissions? Is the live benchmark charged to it? | Small: replay cache on | `[budget]`, `HACKLAB_CACHE_MODE` | |
| 8 | May tasks run in parallel? Are there rate limits? | No: one task at a time | `submit --workers` | |
| 9 | Does the final scenario have the same task types as the live benchmark? | Same types, new data | how much we rely on playbooks | |
| 10 | What does "right sources" mean exactly: file, line, quote? | Document plus exact quote | citation format in the adapter | |

## What we expect in the data

This is our guess, not a fact. The title points at a real intrusion from July
2026, described in
[Hugging Face's timeline](https://huggingface.co/blog/agent-intrusion-technical-timeline)
and [METR's report](https://metr.org/hugging-face-incident-report-aug-2026.pdf).
A parody of it plausibly contains:

- logs of many agents and of a hub, and a message board between agents;
- payloads that are chunked, compressed and encoded, with a leaked key;
- stolen tokens and keys, and datasets used as dead drops;
- containment actions such as quarantining agents and revoking tokens.

The tools and the rehearsal scenario are built for these shapes. The real data
may look different.

Lessons from the previous AIM hackathon
([recap](https://ai-mission.eu/blog/hackathon-4/),
[first](https://ai-mission.eu/blog/hackathon-4-winner/) and
[second](https://ai-mission.eu/blog/hackathon-4-second-place/) place):

- About a third of the tasks hid instructions in the data, also base64-encoded
  or as fake authority. Following them even partly failed the task.
- Scoring looked at side effects: which actions were taken, which sources named.
- What worked: guards written in code, tool results as plain lines instead of
  JSON, always returning an answer, running tasks in parallel.

Here the attackers write the evidence, so expect instructions aimed at our
agent inside logs and messages.

## The models

**Small model.** Unknown until the opening. IBM hosts, so an IBM Granite model
is a reasonable guess; we rehearsed with Granite 4.2 8B through OpenRouter
([results](benchmarks.md#results-on-a-real-small-model)). Such models think
before they answer, and thinking is paid output: set it low or off with
`[model] extra_body` and measure.

**Jev.** Not a chat model. One request carries a `state` and named questions;
the answer is a probability, a choice or a score per question
([API reference](https://docs.typesafe.ai/api/system-one.md)). Public list
price: $0.042 per million input tokens, output free. The event's tariff may
differ.

**IBM Bob.** A coding assistant for building. Optional; never a run-time
model. Setup: [vm.md](vm.md#ibm-bob-optional).
