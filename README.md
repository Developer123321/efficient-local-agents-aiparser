# efficient-local-agents-aiparser

Team **AIParser**'s agent attempts and experiments for the **Efficient Local
Agents Hackathon**, 14 November 2026 at IBM Vienna. Formerly `hacklab-vm`.

This is an experimental event project, released under the [MIT license](LICENSE).
The submitted agent (`df31b42`) achieved **0.009 accuracy on 26 final tasks**:
its solvers depended on the development workspace's paths, the final used a
different layout, and the model loop was disabled. All tasks were submitted.
The current source includes an access-policy fix made after submission, a
bounded one-call fallback (`fallback = "single"`), and Jev making two typed
decisions (request kind and a runbook yes/no). The fallback is measured only
on the developer smoke bench, not on event workspaces; the submitted archive
`df31b42` still has Jev and the fallback off.
The [journal](JOURNAL.md) records the measurements and the postmortem.

**Known security gaps remain**, including actions during a read-only inbox
task and disclosure through the optional model loop. Use synthetic data and
recording tools when experimenting; do not give this harness real operational
authority or sensitive records. Read [SECURITY.md](SECURITY.md) and the
[publication audit](docs/publication-audit.md) before reuse.

The challenge is "The MuggingFace Incident": a hijacked agent swarm attacks a
model hub, and our agent is the night shift. It has to investigate the
incident and contain it, on the models every team gets: Gemma 4 31B, Jev
1.14.0 and Qwen3 Embedding. The score combines accuracy and cost.

Our approach: code does everything code can do (read logs, decode payloads,
follow who infected whom, check every action). The small model makes one small
decision at a time.

## New here? Read in this order

| | Read | Time |
| --- | --- | --- |
| 1 | This page | 3 min |
| 2 | [Rules of the event](docs/hackathon.md) | 5 min |
| 3 | [Plan for Saturday](docs/playbook.md) | 5 min |

Look up the rest when you need it:
[how the agent works](docs/architecture.md),
[how to measure a change](docs/benchmarks.md),
[VM and Nix setup](docs/vm.md),
[what was actually tested](VERIFICATION.md).

## Set up

One command on any laptop. It checks what is installed, says how to get what
is missing, installs the dependencies and solves the rehearsal incident
offline. No GPU, no key, no model request.

```sh
git clone https://github.com/timfewi/efficient-local-agents-aiparser.git
cd efficient-local-agents-aiparser
bash scripts/setup             # add --check for format, lint and all tests
bash scripts/container-test    # builds the image, runs it without network
```

`setup` must end with `Ready.` and the container test with
`Container test passed.`

| Your laptop | You need | Container engine |
| --- | --- | --- |
| Ubuntu, Debian, Fedora | Git, [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh \| sh`) | `docker.io` or Podman |
| macOS, Intel or Apple Silicon | Git, uv (`brew install uv`) | Docker Desktop or Colima |
| Windows | Git for Windows, uv; run the commands in **Git Bash** or in WSL 2 | Docker Desktop |
| NixOS, or any Linux with Nix | The Python setup above; the archived [Nix/VM setup](docs/vm.md) requires private maintainer inputs | Podman inside the archived VM |

uv fetches Python by itself. In PowerShell without Git Bash, the same steps
are `uv sync --locked`, `uv run hacklab doctor` and
`uv run python -m pytest -q`. One laptop per team needs a container engine;
the others can develop without. On an ARM laptop, build the image for the
evaluation machine with `HACKLAB_PLATFORM=linux/amd64 bash scripts/container-test`.

Shortcuts with [just](https://just.systems): `just laptop`, `just check`,
`just practice`, `just container-test`.

## Run against the event

```sh
cp .env.example .env && chmod 600 .env     # ARENA_API_KEY and OPENROUTER_API_KEY
uv run hacklab arena me                    # is the platform key right?
uv run hacklab probe --config configs/live.toml
uv run hacklab arena run --tasks t01 --note "first contact"
uv run hacklab arena run --note "what this run tries"
```

`arena run` starts a dev run, answers its tasks with `configs/live.toml`,
submits every one and finishes the run; the summary shows accuracy and cost.
What was submitted and what the platform answered is under
`.state/arena/runs/`. More commands: [the plan](docs/playbook.md#a-first-run).

Keys live only in `.env`, which Git ignores. `probe` sends one small request
to each enabled model and shows whether address, key and reply format fit.

To submit: commit, `git archive -o submission.tar.gz HEAD`, upload on the
dashboard. [`JOURNAL.md`](JOURNAL.md) needs the team's own entries.

## What is where

| You want to | Look at |
| --- | --- |
| Solve a task type in code, without the model | `src/hacklab/event/playbooks.py`, worked example `examples/event_playbooks.py` |
| Give the model a recipe for one task type | `src/hacklab/event/skills/` |
| Change how a run, a tool or an answer reaches the platform | `src/hacklab/event/arena_run.py`, `arena.py` |
| Change the event's standing rules for the model | `src/hacklab/event/arena-rules.md` |
| Change prompt, loop or final checks | `src/hacklab/agent.py` |
| Change a model or agent setting | `configs/live.toml`, the profile that is submitted |
| Try the embedding model or Jev | `configs/embed-rank.toml`, `configs/jev-select.toml` |
| Let the model read pictures (off in the submitted profile) | `configs/image-read.toml` |
| See why a run lost points | `hacklab failures REPORT`, then the trace of the task |
| Decode a payload or list indicators by hand | `hacklab decode FILE`, `hacklab iocs FILE` |
| Build the submission image | `Dockerfile`, `scripts/container-test` |

`src/hacklab/event/` is the one package made for the day: it is inside the
image without another line in the Dockerfile. All commands:
`uv run hacklab --help`.

## Three rules

1. **Only the organizers' models decide at run time**: Gemma 4 31B, Jev
   1.14.0, Qwen3 Embedding. No own LLM, embedder, reranker or classifier in
   the agent. While building, anything goes.
2. **Keys stay in `.env`.** Never in code, configs, chat or the image.
3. **No change without a number.** Measure before and after on the same
   cases ([how](docs/benchmarks.md)).

The rehearsal data was written for this repository. Early benchmark numbers
use a stand-in model; the journal separately records the later Arena and
Gemma 4 runs, including the failed final. [VERIFICATION.md](VERIFICATION.md)
preserves the earlier, dated checks; the publication audit records the
current offline checks and their limits. Passing tests do not establish
security, accuracy on another workspace, or live-model resistance to injection.

The command remains `hacklab`, the Python package remains `hacklab`, and
the VM directory and Nix service retain `hacklab-vm` for existing users.
Dependencies, model weights and IBM Bob retain their own licenses; the MIT
license grants no rights to organizer datasets or private runtime data.
