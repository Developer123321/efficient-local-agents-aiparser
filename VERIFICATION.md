# What was actually tested

## 14 November 2026, the announced models (same workstation)

The models were announced: Gemma 4 31B, Jev 1.14.0, Qwen3 Embedding. Added:
an optional embedding provider (`[embedding]`, `configs/embed-rank.toml`),
Gemma 4's thought channel in the reply parser, the models' facts in
[docs/hackathon.md](docs/hackathon.md#the-models).

| Check | Result |
| --- | --- |
| `bash scripts/check` (Ruff format and lint, `pytest`) | Passed, **295 tests** on Python 3.15 |
| Shellcheck on `scripts/container-test` | Passed |
| `hacklab probe --config configs/embed-rank.toml` against `hacklab mock-server` over HTTP on the host | Small model and embedding model answered; the embedding receipt has input tokens and 0 output tokens |
| `hacklab submit --config configs/embed-rank.toml` against the stand-in, rehearsal seed 3 | 9 answer lines with the embedding model named, 9 without a model name (`embedding_unavailable` recorded per task) |
| `bash scripts/vm-smoke` (disposable guest, rootless Podman) | Passed, including `scripts/container-test`: the image built, `probe` reached the stand-in embedding model over a local address, and `configs/embed-rank.toml` wrote 9 answers with and 9 without a model name |

The VM ran on the tree before the last two changes: the 15-second limit for
an embedding request with its test, and documentation. The fast gate ran
again after them.

No request went to a real model. The facts about the three models come from
their model cards and vendor documentation, read on 14 November; none was
checked against the event's endpoints.

On the rehearsal incident and on `bench/smoke.jsonl` the embedding step sent
no request: it is asked only when the search finds more hits than fit into
the first prompt outside tables and short documents. Its ordering is covered
by a unit test with a stand-in, not by a benchmark.

## 13 November 2026 (evening), NixOS x86_64 workstation with KVM

Nix 2.35.3, uv 0.13.5, Ruff 0.17.3 on the host and 0.17.4 in the guest and in
the containers. The checks ran on the tree of the hardening commits; after
the VM run only documentation and one default argument (`_briefing(room=None)`)
changed, and the fast gate ran again.

| Check | Result |
| --- | --- |
| `project-check fast` (Ruff format and lint, `pytest`) | Passed, **279 tests** on Python 3.15 |
| The same tests in the guest | Passed on Python 3.15.2, 3.13.18 and 3.11.18 |
| `nix fmt -- --ci`, `nix flake check` | Passed, including `vm-contract`, `host-contract` and `ruff-pin` |
| Shellcheck on every script in `scripts/` | Passed |
| `PORTABILITY_TEST=1 bash scripts/vm-smoke` | Passed: the default guest booted on a disposable disk and ran `scripts/guest-check` |
| `scripts/portability-test` inside the guest | `scripts/setup --check` passed in stock `ubuntu:24.04`, `ubuntu:22.04` and `debian:12` containers: uv from the official installer, Python 3.15.2 fetched by uv, Ruff 0.17.4 through uv, 279 tests each |
| Replay of the recorded model runs (no request sent) | Identical to before the changes: 7, 6 and 5 of 9 on seeds 7, 8, 9 with 31, 30 and 27 calls, all served from the cache |
| Rehearsal incident, 40 seeds | Playbooks answered all 9 tasks of every seed with 0 model calls; seed 7 is byte-identical to the data generated before |
| Wheel contents | `hacklab/event/skills/*.md`, `adapter.py` and `playbooks.py` are in the built wheel |

`scripts/container-test` ran inside the guest with rootless Podman:

- the image built from `python:3.13-slim` and `uv.lock` (151 MiB);
- with `--network none` the playbooks solved 9 of 9 rehearsal tasks;
- `doctor --strict --config configs/live.toml` found the candidate profile's
  playbooks and skills inside the image;
- against a stand-in endpoint on a loopback-only network the agent wrote 9
  answers with the baseline profile and 9 with the candidate profile;
- `probe` reached both the stand-in small model and the stand-in Jev over a
  local address;
- with Jev enabled but no key and no route, the agent wrote 9 answers and no
  error;
- a connection from the container to 1.1.1.1:443 failed;
- the agent ran as uid 10001 and could not write to `/app`.

The guest check also saw: no SSH daemon and no network listener, the host's
loopback out of reach, `just ready`, `just check` and `just practice 7`
passing, and numpy, pandas, pymupdf and duckdb importing from binary wheels.

What the replay showed about the earlier candidate profile: with its limits
(`max_total_tokens = 16000`, `max_actions = 20`) the recorded runs lose the
`secrets` and `contain` tasks to the harness's own budget (7 of 9 became 6,
6 became 5). No request was needed to see it; `live.toml` now carries the
measured limits.

## Against real models

With a temporary OpenRouter key and a TypeSafe key in the ignored `.env`:

- `hacklab probe` reached IBM Granite 4.2 8B through OpenRouter and Jev 1.14
  through TypeSafe's API.
- The rehearsal incident without playbooks on seeds 7, 8 and 9: 18 of 27 tasks
  with low reasoning; the eight document tasks: 8 of 8. Details in
  [docs/benchmarks.md](docs/benchmarks.md#results-on-a-real-small-model).

One run per configuration: these numbers show direction, not significance.
They were measured before the Jev fallback and before the hardening of
13 November; the replay above shows that the hardening sends the same
requests. Nothing was sent to a model while hardening.

## Not verified

- **The event's models.** Nothing was run on Gemma 4 31B or on Qwen3
  Embedding. Prompts, tools and defaults were tuned on Granite 4.2 8B, a much
  smaller stand-in, and three seeds of our own scenario. Whether thinking,
  JSON mode and temperature 0 behave on Gemma 4 as assumed is open, and so is
  whether ordering search hits with the embedding model gains anything.
- **The organizers' side.** Starter kit, task and answer format, the
  container's run contract, how the models are reached inside the container,
  prices, scoring formula, rate limits. The Dockerfile's `CMD` is a
  placeholder.
- **Jev and the embedding model in the final run.** Whether the container
  has a route to them is unknown. The fallbacks were tested with a stand-in,
  not with a real outage.
- **Docker, macOS, Windows, ARM.** The container test ran with rootless
  Podman on x86_64 Linux. `scripts/setup` ran on NixOS and in Ubuntu and
  Debian containers. Docker, Docker Desktop, macOS (bash 3.2), Git Bash, WSL 2,
  Apple Silicon and `HACKLAB_PLATFORM` were written for and shellchecked, not
  run.
- **The new seams against a real kit.** `event/adapter.py` (the kit's model
  function, snapshots, tool calls from playbooks), the tolerant task reader of
  `submit` and the log formats (logfmt, JSON lines without a suffix,
  multi-table JSON) are covered by tests on synthetic data only.
- **Retries against a real rate limit.** The pauses were tested with a fake
  clock.
- **The Bob variant on this tree.** `vm-smoke --bob` last passed one commit
  earlier, when Bob was the default. Bob's license step and login were never
  tested.
- **The host module's start and stop helpers** were last exercised on
  12 November.
- The laptop that goes to the event, and the event Wi-Fi.

## Before travelling

On the laptop you bring: `bash scripts/setup --check` and
`bash scripts/container-test`. The Friday checklist is in
[docs/playbook.md](docs/playbook.md#friday-evening).
