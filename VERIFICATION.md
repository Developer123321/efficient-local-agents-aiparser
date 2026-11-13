# What was actually tested

## 13 November 2026, NixOS x86_64 workstation with KVM

Nix 2.35.3, uv 0.13.5, Ruff 0.17.3. The checks ran on the tree of the commit
that made Bob opt-in and Jev optional at run time; only this file was edited
afterwards.

| Check | Result |
| --- | --- |
| `project-check fast` (Ruff format and lint, `pytest`) | Passed, **246 tests** on Python 3.15 |
| The same tests in the guest | Passed on Python 3.15.2, 3.13.18 and 3.11.18 |
| `nix fmt -- --ci`, `nix flake check` | Passed, including `vm-contract` and `host-contract` (Bob off by default) |
| Shellcheck on `container-test` and `vm-run` | Passed |
| `bash scripts/vm-smoke` | Passed: the default guest, without Bob, booted on a disposable disk and ran `scripts/guest-check` |
| Default flake outputs without access to the Bob packaging | The Nix shell and the VM evaluated with an unfetchable Bob input; the Bob variants did not |
| Rehearsal incident, 40 seeds | Playbooks answered all 9 tasks of every seed with 0 model calls (checked on an earlier tree; playbooks unchanged) |

`scripts/container-test` ran inside the guest with rootless Podman:

- the image built from `python:3.13-slim` and `uv.lock` (151 MiB);
- with `--network none` the playbooks solved 9 of 9 rehearsal tasks;
- against a stand-in endpoint on a loopback-only network the agent wrote 9
  answers;
- `probe` reached both the stand-in small model and the stand-in Jev over a
  local address;
- with Jev enabled but no key and no route, the agent wrote 9 answers and no
  error;
- a connection from the container to 1.1.1.1:443 failed;
- the agent ran as uid 10001 and could not write to `/app`.

The guest check also saw: no SSH daemon and no network listener, the host's
loopback out of reach, `just ready`, `just check` and `just practice 7`
passing, and numpy, pandas, pymupdf and duckdb importing from binary wheels.

## Against real models

With a temporary OpenRouter key and a TypeSafe key in the ignored `.env`:

- `hacklab probe` reached IBM Granite 4.2 8B through OpenRouter and Jev 1.14
  through TypeSafe's API.
- The rehearsal incident without playbooks on seeds 7, 8 and 9: 18 of 27 tasks
  with low reasoning; the eight document tasks: 8 of 8. Details in
  [docs/benchmarks.md](docs/benchmarks.md#results-on-a-real-small-model).

One run per configuration: these numbers show direction, not significance.
They were measured before the Jev fallback was added; the fallback changes
nothing while Jev answers.

## Not verified

- **The event's model.** Granite 4.2 8B is a guess. Prompts, tools and
  defaults were tuned on this one stand-in and three seeds of our own
  scenario.
- **The organizers' side.** Starter kit, task and answer format, the
  container's run contract, how the models are reached inside the container,
  prices, scoring formula, rate limits. The Dockerfile's `CMD` is a
  placeholder.
- **Jev in the final run.** Whether the container has a route to Jev is
  unknown. The fallback was tested with a stand-in, not with a real outage.
- **Docker.** The container test ran with rootless Podman, not with Docker or
  Docker Desktop, and not on macOS, Windows or ARM.
- **The Bob variant on this tree.** `vm-smoke --bob` last passed one commit
  earlier, when Bob was the default. Bob's license step and login were never
  tested.
- **The host module's start and stop helpers** were last exercised on
  12 November.
- The laptop that goes to the event, and the event Wi-Fi.

## Before travelling

On the laptop you bring: `just ready`, `just check`, `just practice` and
`just container-test`. The Friday checklist is in
[docs/playbook.md](docs/playbook.md#friday-evening).
