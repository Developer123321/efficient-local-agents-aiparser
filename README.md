# hacklab-vm · night-shift agent lab

A harness, a rehearsal scenario and a reproducible environment for the
**Efficient Local Agents Hackathon, 14 November 2026 at IBM Vienna**. The
challenge, "The MuggingFace Incident", asks for an agent that investigates and
contains an attack by a hijacked agent swarm on a model hub, on a fixed small
model, scored on accuracy and cost, inside a container without internet.

The harness lets code do what code can do — parsing logs, decoding payloads,
following who infected whom, checking every containment action — and asks the
model for one small typed step at a time. It is preparation to calibrate on
the real model and data, not a measured optimal agent or a winning guarantee.

## Start

With Python 3.11 or newer, [uv](https://docs.astral.sh/uv/) and Git:

```sh
git clone https://github.com/timfewi/hacklab-vm.git
cd hacklab-vm
uv sync --locked
uv run hacklab doctor
uv run hacklab practice --seed 7 --out data/practice-7     # a rehearsal incident
uv run hacklab benchmark --config configs/practice.toml \
  --dataset data/practice-7/cases.jsonl --root data/practice-7/docs \
  --out results/practice-7.json                            # solved by code, 0 model calls
bash scripts/container-test                                # needs Docker or Podman
```

`just ready`, `just practice` and `just container-test` do the same where
`just` is installed. On NixOS or with Nix, `nix develop` provides Python 3.15,
uv, Ruff, Just and the rest; see [VM and laptop setup](docs/vm.md) for the
ready-made QEMU guest with Podman and IBM Bob Shell.

## On the day

```sh
cp .env.example .env && chmod 600 .env
# Enter SMALL_MODEL_BASE_URL, SMALL_MODEL_NAME, SMALL_MODEL_API_KEY (and TYPESAFE_API_KEY).
export HACKLAB_CACHE_MODE=readwrite        # replay unchanged requests while iterating
uv run hacklab probe --config configs/live.toml      # one small request per model
uv run hacklab benchmark --config configs/baseline.toml --dataset bench/dev.jsonl \
  --root bench/dev --out results/baseline.json
uv run hacklab benchmark --config configs/live.toml --dataset bench/dev.jsonl \
  --root bench/dev --out results/live.json
uv run hacklab compare results/baseline.json results/live.json
uv run hacklab failures results/live.json            # which check failed, per category
uv run hacklab budget --config configs/live.toml     # what the allowance has paid for
```

`bench/dev.jsonl` stands for the cases you build from the organizers' tasks.
The hour-by-hour plan, roles and the freeze procedure are in the
[day plan](docs/playbook.md); the questions to ask at the opening are in
[event facts](docs/hackathon.md#questions-for-the-opening).

The generative adapter assumes an OpenAI-compatible `/chat/completions`
endpoint. That is configurable preparation, **not a verified organizer API
contract**. Enter the organizers' prices in the TOML configuration to estimate
cost; without them, live cost stays **unknown**. Bob and other coding
assistants build the agent; at run time only the provided models decide.

## What is in the box

- **Code-first solving.** [Playbooks](src/hacklab/playbooks.py) answer a
  recognised task type without a model call. The
  [rehearsal playbooks](src/hacklab/practice.py) show the pattern.
- **Forensic tools** for the model and for playbooks: layered decoding with
  chunk reassembly, indicator extraction, log tables with filters and
  grouping, "what did each actor do after its trigger event", propagation
  graphs, kill-chain phases.
- **Evidence by ID.** The model cites `e7`; code supplies document, locator
  and exact quote. Secrets and encoded blobs appear as short placeholders
  that code expands again.
- **Guards** against instructions hidden in the data and against containing
  the wrong target, each with its own switch.
- **Budget protection.** A replay cache for development, a team allowance
  across runs, per-task limits, and a receipt for every request.
- **A container** that runs unprivileged and without network, and a test
  that proves it.

## Commands

| Command | Purpose |
| --- | --- |
| `just check` | Format and lint checks and the offline test suite |
| `just practice [seed]` | Generate a rehearsal incident and solve it with the playbooks |
| `just practice-live [seed] [config]` | The same incident against the configured model, with the failure view |
| `just container-test` | Build the image and run it without any network |
| `hacklab doctor` | Local readiness and credential presence; no API request |
| `hacklab probe --config configs/live.toml` | One tracked minimal request to each configured model |
| `hacklab solve --task task.json --root data/ --config …` | Run one task |
| `hacklab benchmark --dataset cases.jsonl --root docs/ --repeat 3 --out results/run.json` | Run cases and track every attempt |
| `hacklab compare results/a.json results/b.json` | Accuracy, cost, calls and tokens on the same data |
| `hacklab failures results/run.json` | Failed checks per category and calls per task |
| `hacklab budget --config …` | Live spend in this tracking database against `[budget]` |
| `hacklab cache --config … stats\|clear` | Inspect or empty the development replay cache |
| `hacklab submit --input tasks.jsonl --output answers.jsonl --root data/ --workers 4` | JSONL adapter; adapt to the organizers' contract |
| `hacklab practice --seed N --out DIR` | Write a rehearsal incident with reference answers |
| `hacklab decode FILE --key K` / `hacklab iocs FILE` | Analyst helpers: peel a payload, list indicators |
| `hacklab mock-server --port 8080` | Stand-in model endpoint for plumbing tests |
| `hacklab embedding-fetch` | Download the EmbeddingGemma ONNX files for the optional embedder |
| `hacklab runs` / `stats` / `export --out FILE` | Inspect local run metadata and usage |

Use `uv run --locked` before `hacklab` outside an activated environment.
`hacklab --db PATH COMMAND` or `HACKLAB_DB` selects the tracking database.
Answers and benchmark reports contain quotes from the data; keep them private.
The tracking database omits document text, answers, prompts and keys. The
development cache holds model replies and stays in the ignored `.state/`
directory.

## Read before the event

- [Event facts, our reading of the scenario, questions for the opening](docs/hackathon.md)
- [Day plan: roles, the iteration loop, the freeze](docs/playbook.md)
- [Architecture: where tokens go, defence layers, tools, extension points](docs/architecture.md)
- [Benchmark method](docs/benchmarks.md) and [tracking semantics](docs/tracking.md)
- [VM and laptop setup](docs/vm.md)
- [Checks actually executed](VERIFICATION.md)

The rehearsal incident and the eight document fixtures in `bench/` are
practice material written for this repository, not the organizers' benchmark.
A perfect score of the playbooks on the rehearsal shows that the tools work on
data built for them. A stand-in small model (IBM Granite 4.2 8B through
OpenRouter) solved 18 of 27 rehearsal tasks with the tools alone and all eight
document fixtures; the numbers and what they changed are in
[benchmarks.md](docs/benchmarks.md#what-a-real-small-model-did-13-november-2026).
Neither says how the event's model will do on the real scenario.
