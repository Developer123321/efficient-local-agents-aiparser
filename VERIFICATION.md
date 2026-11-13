# Verification record

## 2026-11-13 on a NixOS x86_64 workstation with KVM

Nix 2.35.3, uv 0.13.5, Ruff 0.17.3 and the Nixpkgs revision in `flake.lock`.
The checks ran against the tree of the commit that added the measurements
against a real model; only this file was edited after them.

| Check | Observed result |
| --- | --- |
| `project-check fast` (`scripts/check`: Ruff format and lint, `pytest`) | Passed; **241 tests** on Python 3.15.0, one skipped there (the real embedder needs ONNX Runtime, which has no CPython 3.15 wheel) |
| The same tests on other interpreters | 241 passed and one skipped on Python 3.15.2, 3.13.18 and 3.11.18 inside the guest; with the `embeddings` extra on 3.13.18 outside the guest all 242 passed, including the real EmbeddingGemma test |
| `nix fmt -- --ci` | All Nix source formatted |
| `nix flake check` | All outputs evaluated; `vm-contract` (now also: rootless Podman on, Docker daemon off) and `host-contract` built and passed |
| `bash scripts/vm-smoke` | Passed: guest with Bob Shell booted under KVM on a disposable disk and ran `scripts/guest-check` over its console, including the container test |
| Shellcheck 0.11.0 | `container-test`, `guest-check`, `vm-run`, `vm-smoke` and `vm-skills` passed |
| `uv build` | Source distribution and wheel built |
| Rehearsal incident, 40 seeds, 15 to 132 agents, 350 to 6,200 audit events | The code-only playbooks answered all 9 tasks of every seed correctly with 0 model calls |
| Rehearsal incident with 300 agents and 60,000 audit events | 9 of 9; slowest task about 3 s |

Inside the booted guest `scripts/guest-check` observed:

- Python 3.15.2 as `python3`, and `python3.14`, `python3.13`, `python3.11`
  (3.14.10, 3.13.18, 3.11.18); uv 0.13.5;
- `--memory 6144 --cpus 3` taking effect; `bob --version` answering;
- no running SSH daemon and no TCP or UDP listener beyond loopback and the
  DHCP client; a refused connection to a port on the host's loopback;
- the project seeded at `~/workspace/hacklab-vm` with the `typesafe-ai` skill
  linked; `just ready`, `just check`, the mock benchmark and
  `just practice 7` with accuracy 1.0;
- the test suite on Python 3.13 and 3.11 in separate environments;
- `import numpy, pandas, pymupdf, duckdb` from binary wheels;
- `scripts/container-test` with rootless Podman: the image built from
  `docker.io/library/python:3.13-slim` and `ghcr.io/astral-sh/uv:0.13.5`
  (151 MiB); with `--network none` it generated a rehearsal incident and the
  playbooks solved 9 of 9; against the stand-in endpoint in a shared
  loopback-only network namespace `submit --workers 3` wrote 9 answers; a
  connection from that namespace to 1.1.1.1:443 failed; the agent ran as uid
  10001 and could not write to `/app`.

The test suite covers, beyond the earlier document, provider and tracking
tests: layered decoding with known and single-byte XOR keys, bounds and
false-positive cases; chunk reassembly in both chunking orders with checksums
and gaps; indicator extraction including defanged forms; timestamp shapes and
row filters; causal reach and origins in timed graphs; phase rules; injection
patterns, also behind encodings, and benign log lines that must not be
flagged; placeholders and their expansion; the action guard's rules;
evidence IDs, citations and grounding; every tool through the toolkit;
external tools and the guard on mutating ones; reply and argument variants of
small models; playbooks, including a failing solver; the replay cache, replay
mode and the development allowance; set and action scoring and the failure
view; the rehearsal generator's reproducibility and traps; the CLI commands;
the stand-in endpoint over real loopback HTTP.

Checked on 2026-11-12 and not repeated on this tree: the host module's start
and stop helpers against a disposable guest (the module is unchanged), the
guest variant without Bob (`bash scripts/vm-smoke --no-bob`), and the
binary-wheel survey on Python 3.15.

### Against real models

With a temporary OpenRouter key and a TypeSafe key, both kept in the ignored
`.env`:

- `hacklab probe`: IBM Granite 4.2 8B (`ibm-granite/granite-4.2-8b`) through
  OpenRouter and Jev 1.14 through `api.typesafe.ai/v1/systemone` both
  answered; usage, cached and reasoning tokens were recorded.
- The rehearsal incident without playbooks, seeds 7, 8 and 9, and the eight
  document fixtures; with reasoning off and low; with and without Jev as
  final judge. Results, costs and what the traces changed are in
  [docs/benchmarks.md](docs/benchmarks.md#what-a-real-small-model-did-13-november-2026):
  18 of 27 incident tasks and 8 of 8 document tasks with reasoning low.
  Reasoning high, a visible thought and schema-enforced replies were
  measured on two seeds each and brought no gain.
- EmbeddingGemma (official ONNX export, 8-bit) loaded in 1.7 s on CPU,
  reproduced the model card's similarity ranking and ranked three paraphrased
  questions onto the right runbook lines. On the document fixtures it changed
  no prompt and therefore no result.

One run per configuration at temperature 0: these numbers show direction,
not significance.

### Not verified

- **The event's model.** Granite 4.2 8B is a guess at its kind. Prompts, tool
  list, guards and defaults were tuned on this one stand-in and three seeds
  of a scenario written here; they may fit another model or the real data
  worse. Jev's passage filter and a temperature above 0 were not measured.
- **The organizers' side.** The starter kit, its tools, the task and answer
  format, the container's run contract, how the model is reached inside the
  container, prices, score normalisation, rate limits and the platform's
  submission path are unknown. The Dockerfile's `CMD` is a placeholder.
- **The rehearsal's relevance.** The incident generator and its playbooks
  were written together from public reports of the July 2026 intrusion. A
  perfect score there shows that the tools work on data built for them.
- **Docker.** The container test ran with rootless Podman in the guest, not
  with Docker or Docker Desktop, and not on macOS, Windows or ARM. The image
  was tested without the `embeddings` extra and without a baked model.
- Injection patterns against attacks other than those written for the tests;
  XOR payloads with an unknown key longer than one byte.
- Bob's license acceptance and login, and whether Bob activates the linked
  skill. `hacklab-vm view` in a real terminal on an activated host.
- The laptop that will be used at the event, its KVM access and the event
  Wi-Fi.

## Before travelling

On the laptop you will bring: `just ready`, `just check`, `just practice` and
`just container-test`; with the VM, `bash scripts/vm-smoke`, then
`bash scripts/vm-run`, set the console password and complete Bob's license and
login steps from [docs/vm.md](docs/vm.md#ibm-bob). The checklist for Friday
evening is in [docs/playbook.md](docs/playbook.md#friday-evening).
