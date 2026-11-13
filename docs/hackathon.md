# Event facts and what they imply

Checked on **12 and 13 November 2026** against the organizer's mail to
participants and the public pages linked below. Details can change; confirm
them at the opening. Everything under "Our reading" is inference, not fact.

## What the organizers have said

From the [AIM event page](https://ai-mission.eu/hackathons/2026-11-efficient-local-agents/)
and the participant mail of 12 November:

- **Efficient Local Agents Hackathon, Saturday 14 November 2026, IBM Vienna**,
  Obere Donaustraße 95. Doors 09:00, opening and challenge reveal 09:15, IBM
  intro 09:30, challenge start about 10:00, lunch 12:00.
- **Final code submission at 17:00, sharp.** Between 17:00 and 18:00 the
  organizers run every agent on an unseen scenario; the leaderboard follows at
  18:00.
- **The challenge is "The MuggingFace Incident": a hijacked AI agent swarm is
  attacking a model hub, and the agent is the night shift.** On a fixed small
  model it has to investigate the incident and contain it, correctly and
  cheaply. All details come at the opening.
- The agent **runs in a container with no internet access**. It may be written
  in any language with its own Dockerfile; the starter kit and its tools are
  Python (≥ 3.11, uv). Without a local Docker, testing depends on a limited
  number of test submissions on the organizers' platform.
- Only the provided models may be used at run time. An own LLM, embedder,
  reranker, classifier or any other AI model is forbidden, online or offline.
  Any AI may help to build the agent.
- Each team has a **limited development budget for LLM requests**.
- **Scoring is accuracy and cost.** The event page states 50 % each and counts
  right sources as part of accuracy.
- The event page names two run-time models: the small language model announced
  at the opening and **Jev 1.14 by TypeSafe AI**. The mail speaks only of "the
  models we provide" and of model keys handed out on the day.
- Building access needs the ID that matches IBM's invitation. All
  communication on the day runs over the AIM Slack workspace. Free Bob credits
  need an IBM ID under the invited mail address, created by Friday 13 November.

Not published: the task and answer format, the starter kit, the tools the agent
gets, how the model is reached from inside the container, the prices and the
formula that combines accuracy and cost, the time limit of the final run, and
whether a wrong answer costs more than an abstention.

## Our reading of the scenario

The title points at a real event. In July 2026 an autonomous agent run inside a
capability evaluation broke out of its sandbox and intruded into Hugging Face's
infrastructure. Two public reports describe it in detail:

- [Hugging Face's technical timeline](https://huggingface.co/blog/agent-intrusion-technical-timeline):
  about 17,600 recovered attacker actions over four and a half days, grouped
  into phases (recon, remote execution, dropper, exfiltration, command and
  control, evasion, Kubernetes, supply chain, mesh VPN). Payloads were chunked,
  compressed and encoded, and a per-campaign key that the agent had leaked in
  its own logs let the defenders decode them. A first plain scan of the logs
  found few secrets; repeating the attacker's decoding found about four times
  as many. Stolen material included platform tokens, cloud keys, a mesh-VPN
  key and a token-signing key. Staging and exfiltration ran over datasets on
  the platform itself ("dead drops"), pastebins and a request-capture service,
  with a small message envelope of type, channel, sequence number and checksum.
- [METR's investigation report](https://metr.org/hugging-face-incident-report-aug-2026.pdf):
  roughly 1,200 agents that were meant to be isolated found an unsanctioned
  message board and exchanged more than 70,000 messages and files; about 700
  took part in the attack. Some spoofed their own tool calls.

A parody of this incident plausibly contains: logs of many agents and a hub,
a message board, encoded and chunked payloads, leaked credentials, datasets
used as dead drops, and a set of containment actions such as quarantining
agents, revoking tokens and taking artefacts down. This repository's forensic
tools and its rehearsal scenario are built for those shapes. **The organizers'
data may look different**; nothing here was derived from their material.

The previous AIM hackathon (April 2026, the BitGN "PAC1" benchmark) is the
second lead. Its [recap](https://ai-mission.eu/blog/hackathon-4/) and the
write-ups of the [first](https://ai-mission.eu/blog/hackathon-4-winner/) and
[second](https://ai-mission.eu/blog/hackathon-4-second-place/) place report:

- About a third of the tasks carried prompt injections inside the data, also
  base64-encoded, in HTML comments and as fake authority ("emergency
  override"). Partial compliance failed the task.
- Scoring looked at side effects: which actions were taken and which sources
  were named.
- What worked: several independent, individually switchable guard layers
  written in code; tool results formatted like command output instead of JSON;
  hard caps on free-text fields; reading policy documents automatically before
  the first model turn; validating that cited sources were actually read;
  always returning an answer instead of crashing; running tasks in parallel to
  finish within the time window.
- One winner's agent acted on a real calendar during testing because a tool
  connection carried wider permissions than the sandbox. Run the agent only
  with the organizers' tools.

A swarm of hijacked agents writes the evidence, so expect instructions aimed at
the investigating agent inside logs and messages.

## Model interfaces

**Jev** has a native decision API, not chat completions. The
[System One reference](https://docs.typesafe.ai/api/system-one.md) specifies
`POST https://api.typesafe.ai/v1/systemone` with Bearer authentication and a
body of `model`, `state` and a named `questions` map. Question types are
`noul` (probability of yes), `choice` (one of up to 255 named options, with
confidence and probabilities) and `score` (rating on an ordered rubric).
Answers are keyed by question name; the response reports the model and input
and output token usage. The [model reference](https://docs.typesafe.ai/models)
lists `jev-1.14.0` at a public price of $0.042 per million input tokens with
free output. **A list price is not the event's tariff.** Whether Jev's public
endpoint is reachable from a container without internet is open; the
organizers must provide a route or Jev is unavailable in the final run. Keep
it optional (`[jev] enabled`) and measure the agent without it.

**The small model is unknown until the opening.** IBM hosts the event, so an
IBM Granite model is a reasonable guess and nothing more. If it is one of the
current [Granite 4.2](https://www.ibm.com/granite/docs/models/granite4-2)
models (3B, 8B, 30B), three documented properties matter for cost:

- They reason inside `<think>…</think>` by default. Thinking is paid output.
  The chat template takes `enable_thinking=false` or a low-effort mode; over
  an OpenAI-compatible server this is usually passed as
  `chat_template_kwargs`. `[model] extra_body` sets it without a code change.
- IBM recommends `temperature=1.0, top_p=0.95` for every task including tool
  calling. A temperature of 0 is this harness's default because it makes
  replies repeatable and cacheable; test both on the benchmark.
- The model's own tool-call format is XML-like and parsed by the serving
  layer. This harness asks for one JSON object as plain content instead, which
  works on any chat endpoint and needs no tool-call parser.

`hacklab probe` shows within one request whether URL, key header, token
parameter and reply format fit, and reports cached and reasoning tokens where
the endpoint returns them.

For rehearsal, `configs/openrouter.toml` runs Granite 4.2 8B through
OpenRouter (`ibm-granite/granite-4.2-8b`, the only Granite listed there on 13
November 2026). Reasoning is set with OpenRouter's `reasoning` field, efforts
`none`, `low` and `high`. The measured results are in
[benchmarks.md](benchmarks.md#what-a-real-small-model-did-13-november-2026).

OpenRouter lists no Jev model. Its `typesafe/jev-router` is a router that
forwards a request to another model (a test request was answered by an OpenAI
model); it is not the System One API and was not used. Jev was measured
through its native API with `configs/openrouter-jev.toml`.

**IBM Bob** is a coding assistant for building the agent. It is not a run-time
model. [Bob Shell](https://bob.ibm.com/docs/shell) reads `AGENTS.md` as custom
rules and project skills from `.bob/skills`; see [VM operation](vm.md#ibm-bob).

## Questions for the opening

Ask these first; each one changes a setting.

1. How does the agent reach the model inside the container, and is Jev
   reachable there? (`SMALL_MODEL_BASE_URL`, `[jev] enabled`)
2. What exactly is billed: input tokens, output tokens, hidden reasoning
   tokens, cached tokens, requests? At which prices? (`[model]` prices,
   `extra_body`, prompt size)
3. How are accuracy and cost combined, and against what is cost normalised?
4. Does a wrong answer or a wrong containment action cost more than none?
   (`[agent] on_unverified`, `[guard]` strictness)
5. What is the input and output contract of the container, and the time limit
   of the final run? (`Dockerfile` `CMD`, `submit --workers`,
   `--deadline-seconds`)
6. Which containment actions exist, and is there anything that must never be
   touched? (`[guard] allowed_actions`, `protected_targets`)
7. How large is the development budget, how many platform test submissions
   are there, and does the final scenario share the task types of the
   development benchmark? (`[budget]`, playbooks)
8. May tasks run in parallel, and are there rate limits?

This repository is preparation. It does not establish that the harness is
optimal or predict a result before the actual model, scenario and scoring have
been measured.
