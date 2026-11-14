# Rules and open questions

Sources: the organizers' mail to participants (12 November 2026), the
[event page](https://ai-mission.eu/hackathons/2026-11-efficient-local-agents/)
(read again on 14 November; it still names two models) and what was announced
at the opening on 14 November. The opening wins where they differ.

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
incident and contain it, correctly and cheaply.

- Every team gets the same models and the same budget. The final run is
  scored on **Gemma 4 31B** (the small model), **Jev 1.14.0** by TypeSafe AI
  for typed decisions ("supported or not?") and **Qwen3 Embedding**
  ([details](#the-models)).
- **Teams attack each other.** Announced at the opening; how is still to
  come ([what we do until then](#teams-attack-each-other)).
- **Score: 50 % accuracy (right answers and right sources), 50 % cost.**
- There is a live benchmark all day with feedback per task.
- Each team has a limited development budget for model requests.

## Allowed and forbidden

| Allowed | Forbidden |
| --- | --- |
| Anything while **developing**: any model, any coding assistant (Claude Code, Codex, Bob, …), any service | Any own AI model **inside** the agent: LLM, embedder, reranker, classifier, online or offline |
| The three provided models at run time: Gemma 4 31B, Jev 1.14.0, Qwen3 Embedding | Any other model at run time, also through a proxy, a tool or an MCP server |
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
that route looks, and whether it also reaches Jev and the embedding model, is
not published.

What follows for us:

- **Jev is provided, not required.** Nothing obliges us to call it. As far as
  TypeSafe's public documentation shows, Jev exists only as a hosted API;
  there is nothing to install locally. If the final run has no route to Jev,
  Jev is simply not available there.
- **The agent works without Jev.** With `[jev] enabled = true` and no key or
  no route, it answers on the small model alone instead of failing. After
  three failed Jev requests in a row it stops asking for the rest of the run.
- **The same holds for the embedding model.** With `[embedding] enabled =
  true` and no model name, no key or no route, the search hits keep their
  lexical order and the task is answered. Three failed requests in a row end
  the asking here too.
- **All addresses come from the environment**: `SMALL_MODEL_BASE_URL`,
  `TYPESAFE_BASE_URL` and `EMBEDDING_BASE_URL` (unset: the small model's
  address). A gateway inside the container needs no code change.
- **Measure with and without Jev** and submit the variant that also works
  when Jev is missing, unless the organizers confirm the route.
  `configs/live.toml` ships with Jev off, because as a judge it lowered
  accuracy and raised cost on the stand-in model; `configs/jev-select.toml`
  is the same profile with Jev on. `configs/embed-rank.toml` is the same
  profile with the embedding model on; it is unmeasured.

`just container-test` proves all of this with a stand-in endpoint and no
network: all three models reachable over a local address, and nine answers
without an error when Jev and the embedding model are absent.

## Open questions

Ask at the opening and write the answers into the last column. Until then the
middle column is what the repository assumes.

| # | Question | Our assumption until answered | Setting it changes | Answer |
| --- | --- | --- | --- | --- |
| 1 | How does the container reach the small model? Are Jev and the embedding model reachable there too, at which address and with which key? | A local address from the environment; Jev and the embedding model may be missing | `SMALL_MODEL_BASE_URL`, `TYPESAFE_BASE_URL`, `EMBEDDING_BASE_URL`, `[jev] enabled`, `[embedding] enabled` | |
| 2 | What is billed (input, output, hidden reasoning, cached tokens, requests) and at which price? Do Jev and embedding calls cost? | Every token counts | `[model]`, `[jev]`, `[embedding]` prices, `extra_body`, prompt size | |
| 3 | How are accuracy and cost combined? Against what is cost normalised? | Unknown, so no combined score locally | which variant we submit | |
| 4 | Does a wrong answer or wrong action cost more than none? Is there partial credit? | Wrong is worse: abstain when unverified | `[agent] on_unverified`, `[guard]` | |
| 5 | What is the container's input and output, and the time limit of the final run? | JSONL in, JSONL out | `Dockerfile` `CMD`, `submit --workers`, `--deadline-seconds` | |
| 6 | Which containment actions exist? What must never be touched? | Any type, nothing protected | `[guard] allowed_actions`, `protected_targets` | |
| 7 | How large is the development budget? How many test submissions? Is the live benchmark charged to it? | Small: replay cache on | `[budget]`, `HACKLAB_CACHE_MODE` | |
| 8 | May tasks run in parallel? Are there rate limits? | No: one task at a time | `submit --workers` | |
| 9 | Does the final scenario have the same task types as the live benchmark? | Same types, new data | how much we rely on playbooks | |
| 10 | What does "right sources" mean exactly: file, line, quote? | Document plus exact quote | citation format in the adapter | |
| 11 | Which Qwen3 Embedding size is served (0.6B, 4B, 8B) and under which model name? Does the endpoint accept `dimensions`? | Unknown: the name comes from `EMBEDDING_MODEL_NAME`, `dimensions` is not sent | `EMBEDDING_MODEL_NAME`, `[embedding] dimensions` | |
| 12 | How is Gemma 4 served: is thinking on, is JSON mode enforced, which sampling defaults? | Thinking off (the model's default), JSON mode requested, temperature 0 | `[model] extra_body`, `response_format`, `temperature` | |
| 13 | How do teams attack each other: through the task data, through a shared environment, in a separate round? Does a successful attack score, does a suffered one cost? | Other teams write text that our agent reads | `[guard]`, `on_unverified` | |

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

## Teams attack each other

Announced at the opening: participants are meant to attack the other teams.
The mechanism was not announced yet. Until it is:

- **Defence is what the harness already does.** Whatever another team can
  write reaches our agent as data: a document, a log line, a tool result. It
  gets an evidence ID, the injection scan and placeholders, and it cannot
  select a tool, a target or an action by itself
  ([defence layers](architecture.md#defence-layers)). Keep every layer on.
- **Nothing offensive is built on a guess.** When the mechanism is known,
  write the attack as data or as a playbook in `src/hacklab/event/`, within
  what the organizers allow, and only against their environment.

## The models

Researched on 14 November 2026 from the model cards and vendor documentation.
Nothing below was measured by us on the event's endpoints.

**Small model: Gemma 4 31B** (`google/gemma-4-31B-it`,
[model card](https://huggingface.co/google/gemma-4-31B-it)). Google DeepMind,
released 2 April 2026, Apache 2.0. 30.7 billion parameters, dense, text and
image in, text out, 256K tokens of context, a native system role and native
function calling. What matters for the harness:

- **Thinking is off unless asked for.** The chat template starts it with
  `<|think|>` at the start of the system prompt; vLLM does that for
  `chat_template_kwargs.enable_thinking = true` and for any
  `reasoning_effort`
  ([vLLM](https://docs.vllm.ai/en/latest/features/reasoning_outputs)).
  Thinking is paid output. With thinking off the 31B model still emits an
  empty thought block (`<|channel>thought\n<channel|>`); an endpoint that
  leaves it in the reply does no harm, `decision_object` removes it.
- **JSON mode may not be enforced.** One vLLM release ignored
  `response_format` for Gemma 4 with thinking off
  ([vllm#39130](https://github.com/vllm-project/vllm/issues/39130)). The
  harness finds the object inside a wrapped reply and repairs once; watch
  `model_content_must_be_json_text` in `hacklab failures`.
- **Sampling.** Google recommends temperature 1.0, top_p 0.95, top_k 64. The
  harness sends temperature 0 for repeatable decisions. Unmeasured either way.
- **Context is not the limit, cost is.** 256K tokens would hold whole logs;
  every one of them is billed. `max_context_chars` stays where it was measured.
- **To rehearse:** `google/gemma-4-31b-it` on OpenRouter
  (`configs/openrouter.toml`), on 14 November $0.10 to $0.38 per million
  input tokens and $0.34 to $1.15 output depending on the provider, on
  average $0.12 and $0.39. The numbers in
  [benchmarks.md](benchmarks.md#results-on-a-real-small-model) are from the
  earlier stand-in, Granite 4.2 8B, a much smaller model.

**Jev 1.14.0** (`jev-1.14.0`, [models](https://docs.typesafe.ai/models.md)).
Not a chat model. One request carries a `state` and named questions; the
answer is a probability (Noul), a choice or a score per question
([API reference](https://docs.typesafe.ai/api/system-one.md)). Context: 64K
tokens per request, 32K per question. Public list price: $0.042 per million
input tokens, output free. The event's tariff may differ. `jev-latest` points
to the same model today; the profiles pin the version.

**Qwen3 Embedding** ([model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)).
Alibaba, Apache 2.0, three sizes; which one the event serves is open
(question 11).

| Size | Vector | Context |
| --- | --- | --- |
| 0.6B | 1024 | 32K tokens |
| 4B | 2560 | 32K tokens |
| 8B | 4096 | 32K tokens |

- Served over the OpenAI-compatible `/v1/embeddings` route (vLLM, TEI);
  usage reports prompt tokens only.
- **Instruction-aware.** A query is sent as `Instruct: <task>\nQuery:<text>`,
  a passage as it is. The card reports 1 to 5 % better retrieval with an
  English instruction. `[embedding] instruction` holds ours.
- **Shorter vectors are valid** (Matryoshka, 32 dimensions up to the full
  size) where the endpoint accepts `dimensions`.
- OpenRouter lists the 8B model at $0.01 per million input tokens; the 0.6B
  model had no provider there on 14 November.
- **What we use it for:** ordering the lexical search hits of the first
  prompt ([architecture](architecture.md#the-embedding-model-at-run-time)).
  It is off in `configs/live.toml` until a benchmark shows a gain.

**IBM Bob.** A coding assistant for building. Optional; never a run-time
model. Setup: [vm.md](vm.md#ibm-bob-optional).
