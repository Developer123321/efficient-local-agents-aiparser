# hacklab-vm

Our agent for the **Efficient Local Agents Hackathon**, Saturday 14 November
2026 at IBM Vienna.

The challenge is "The MuggingFace Incident": a hijacked agent swarm attacks a
model hub, and our agent is the night shift. It has to investigate the
incident and contain it, on a small model that every team gets. The score is
half accuracy and half cost.

Our approach: code does everything code can do (read logs, decode payloads,
follow who infected whom, check every action). The small model makes one small
decision at a time.

## New here? Read in this order

| | Read | Time |
| --- | --- | --- |
| 1 | This page | 3 min |
| 2 | [Rules and open questions](docs/hackathon.md) | 5 min |
| 3 | [Plan for Saturday](docs/playbook.md) | 5 min |

Look up the rest when you need it:
[how the agent works](docs/architecture.md),
[how to measure a change](docs/benchmarks.md),
[VM and Nix setup](docs/vm.md),
[what was actually tested](VERIFICATION.md).

## Set up

You need Git, Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). For
the container test you also need Docker or Podman. No GPU and no key.

```sh
git clone https://github.com/timfewi/hacklab-vm.git && cd hacklab-vm
uv sync --locked
uv run hacklab doctor          # what is installed and configured
bash scripts/check             # format, lint and tests
uv run hacklab practice --seed 7 --out data/practice-7
uv run hacklab benchmark --config configs/practice.toml \
  --dataset data/practice-7/cases.jsonl --root data/practice-7/docs \
  --out results/practice-7.json
bash scripts/container-test    # builds the image, runs it without network
```

The benchmark must report `"accuracy": 1.0` with 0 model calls, and the last
command must end with `Container test passed.`

Shortcuts with [just](https://just.systems): `just ready`, `just check`,
`just practice`, `just container-test`. With Nix, `nix develop` provides
Python, uv, Ruff and Just; a ready-made VM is described in
[docs/vm.md](docs/vm.md). Neither is required.

## Talk to a model

```sh
cp .env.example .env && chmod 600 .env     # then enter URL, model name and key
uv run hacklab probe --config configs/live.toml
```

`probe` sends one small request to each model and shows whether URL, key and
reply format fit. Keys live only in `.env`, which Git ignores.

To rehearse before the event, put an OpenRouter key into `.env` as
`SMALL_MODEL_API_KEY` and run `just practice-live 7 configs/openrouter.toml`.

## What is where

| You want to | Look at |
| --- | --- |
| Solve a task type in code, without the model | `src/hacklab/playbooks.py`, template `examples/event_playbooks.py` |
| Give the model a recipe for one task type | `examples/skills/`, setting `[agent] skills` |
| Plug in the organizers' starter kit | `examples/kit_adapter.py` |
| Change prompt, loop or final checks | `src/hacklab/agent.py` |
| Change a model or agent setting | `configs/*.toml` |
| Decode a payload or list indicators by hand | `hacklab decode FILE`, `hacklab iocs FILE` |
| Build the submission image | `Dockerfile`, `scripts/container-test` |

All commands: `uv run hacklab --help`.

## Three rules

1. **Only the organizers' models decide at run time.** No own LLM, embedder,
   reranker or classifier in the agent. Coding assistants help us build.
2. **Keys stay in `.env`.** Never in code, configs, chat or the image.
3. **No change without a number.** Measure before and after on the same
   cases ([how](docs/benchmarks.md)).

Everything here is preparation. The rehearsal data was written for this
repository; nothing was measured on the event's model or data.
