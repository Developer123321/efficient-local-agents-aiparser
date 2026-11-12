# python-vm · Efficient Local Agents lab

A reproducible Python 3.15 development environment and bootable NixOS/QEMU VM
for the **14 November 2026 Efficient Local Agents Hackathon at IBM Vienna**.
The starter harness keeps decisions typed, tools bounded, sources verifiable
and every model request measurable. It is a starting point to calibrate on the
announced model and scenario, not a measured optimal agent or a winning guarantee.

## Start in an existing Linux / Lab VM

```sh
git clone https://github.com/timfewi/python-vm.git
cd python-vm
nix develop
uv sync --locked
uv run --locked hacklab doctor
uv run --locked hacklab benchmark --config configs/mock.toml --out results/mock.json
uv run --locked hacklab solve --task bench/task.example.json --root bench/data
```

With Python 3.15 or 3.14, `uv` and `ruff` already installed, omit `nix develop`.
Python packages are locked by `uv.lock`; `uv sync --locked --offline` works after
one complete successful sync on that machine. No GPU or model download is required.

## Enable development memory and code navigation

The default Nix shell and VM include Ichnora, AST Index, agent-evidence,
agent-tool-meter and Tabula, pinned to their own source revisions.

```sh
nix develop                 # Or nix develop .#with-bob
uv sync --locked
just dev-setup              # Start/enroll local memory, index code, configure MCP
just dev-status
# Start your coding assistant in this checkout; Bob reads .bob/mcp.json.
```

Ichnora retains development decisions and task checkpoints across sessions.
AST Index supplies bounded structural code navigation. `just index` explicitly
refreshes the code snapshot; `just dev-down` stops this project's memory process.
Both use private state outside the repository. The competition agent continues
to use only its current task's evidence. See [tool integration](docs/tools.md)
for lifecycle, MCP, scope and actual verification.

## Boot a separate VM

```sh
bash scripts/vm-smoke                # Once per machine: boot a disposable guest and check it
bash scripts/vm-run                  # Persistent guest with IBM Bob Shell; --no-bob without
ssh -p 2222 hackathon@127.0.0.1      # vm-run authorizes your host's public SSH key
```

On a NixOS host, `nixosModules.host` provides the same guest as an on-demand
service with `python-vm start|stop|view`. The guest seeds this project into
`~/workspace/python-vm` with the selected skills linked for Bob; `just ready`
there syncs dependencies and prepares development memory, code index and MCP. See [VM operation](docs/vm.md) for persistence, SSH,
`--memory`/`--cpus`, Bob's license and login steps, project transfer and running
on your Security Laptop or inside the Lab VM. Running the development shell
directly inside the existing Lab VM avoids nested virtualization.

## At the hackathon

```sh
cp .env.example .env
chmod 600 .env
# Edit .env locally: SMALL_MODEL_BASE_URL, SMALL_MODEL_NAME, SMALL_MODEL_API_KEY,
# TYPESAFE_API_KEY. Use the endpoint and model provided by the organizers.
uv run --locked hacklab doctor --config configs/live.toml
uv run --locked hacklab solve --config configs/live.toml \
  --task bench/task.example.json --root bench/data
uv run --locked hacklab benchmark --config configs/live.toml --out results/live.json
uv run --locked hacklab stats
```

The generative adapter assumes an OpenAI-compatible `/chat/completions` endpoint;
this is configurable preparation, **not a verified organizer API contract**.
Jev uses its real native `/v1/systemone` API. Enter actual organizer prices in the
TOML configuration to estimate cost; without them, live cost remains **unknown**.
Bob is the coding assistant that builds the agent, not a competition model; at
runtime only the organizers' small model and Jev decide. Bob's license and
login are runtime steps described in [VM operation](docs/vm.md#ibm-bob).

## Commands

| Command | Purpose |
| --- | --- |
| `just check` | Format/lint checks and offline regression tests |
| `bash scripts/vm-smoke` | Boot a disposable guest and run the checks inside it |
| `just ready` | Dependency sync, `dev-setup` and `doctor` in one step |
| `just dev-setup` / `just dev-status` | Prepare and check local development memory, AST and MCP |
| `just index` / `just dev-down` | Refresh code navigation / stop project memory |
| `hacklab doctor` | Local readiness and credential-presence checks; no API requests |
| `hacklab solve --task task.json --root data/ --config configs/live.toml` | Run one task |
| `hacklab benchmark --dataset bench/smoke.jsonl --repeat 3 --out results/run.json` | Run local fixtures and track individual attempts |
| `hacklab compare results/baseline.json results/candidate.json` | Compare accuracy, cost and p95 on the same data |
| `hacklab runs` / `hacklab stats` | Inspect local run metadata and usage |
| `hacklab export --out results/tracking.json` | Export metadata without request/response bodies |
| `hacklab submit --input tasks.jsonl --output results/answers.jsonl --root data/` | Local JSONL adapter; adapt to the organizer's actual contract |

Use `uv run --locked` before `hacklab` outside an activated environment. Set
`hacklab --db /private/directory/tracking.sqlite COMMAND` to select a tracking DB.
Solve/submit output contains answers and quotes; store it privately. The tracking
database intentionally omits document text, answers, prompts and keys.

## Read before the event

- [Verified event facts and day plan](docs/hackathon.md)
- [Architecture and extension points](docs/architecture.md)
- [Benchmark method and optimization](docs/benchmarks.md)
- [Tracking semantics](docs/tracking.md)
- [Existing tools and skills](docs/tools.md)
- [Checks actually executed](VERIFICATION.md)

The included eight synthetic fixtures are development exercises, not the official
benchmark. Offline `mock` runs test plumbing and citation handling; their scores
say nothing about the quality of the provided small model.
