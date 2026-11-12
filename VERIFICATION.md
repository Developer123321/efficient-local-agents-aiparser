# Verification record

## 2026-11-12 on a NixOS x86_64 workstation with KVM

Nix 2.35.3, uv 0.13.5 and the Nixpkgs revision in `flake.lock`. These checks ran
against the working tree that introduced Python 3.15 and the VM changes.

| Check | Observed result |
| --- | --- |
| `project-check fast` in `nix develop` | Passed on Python 3.15.2: Ruff 0.17.4 format/lint and **99 Python tests** |
| Same tests on the fallback interpreter | `export UV_PYTHON=3.14`, `uv sync --locked`, `pytest`: 99 passed on Python 3.14.10 |
| `nix fmt -- --ci` | All Nix source formatted |
| `nix flake check` | All outputs evaluated; `vm-contract` built and passed |
| `nix build .#dev-tools` | Ichnora, AST Index, agent-evidence, agent-tool-meter and Tabula built from their locked sources |
| `nix build .#vm-with-bob`, `.#vm` | Both VM runners built |
| `bash scripts/vm-smoke` | Passed: guest with Bob Shell booted under KVM on a disposable disk in about one minute |
| `bash scripts/vm-smoke --no-bob` | Passed for the variant without Bob |
| Shellcheck 0.11.0 | `vm-run`, `vm-smoke` and `vm-skills` passed |

Inside each booted guest the smoke test observed:

- SSH login with the public key that `vm-run` passed at boot, without a console step;
- Python 3.15.2 as `python3`, Python 3.14.10 as `python3.14`, uv 0.13.5;
- `--memory 6144 --cpus 3` taking effect (3 vCPUs, 5923 MiB visible);
- `bob --version` answering in the Bob variant;
- the project seeded at `~/workspace/python-vm` with `origin` set and 13 skills
  linked, including `typesafe-ai`;
- `uv sync --locked` from PyPI through QEMU user networking;
- `just check` with 99 tests passing, `hacklab doctor` and the mock benchmark;
- `import numpy, pandas, pymupdf, duckdb` from binary wheels.

Binary wheels were also checked on the Nix Python 3.15.2 interpreter outside
the guest. Of 25 packages likely to be needed on the day, 24 installed from
wheels; PyYAML has no CPython 3.15 wheel and installed from source as pure
Python. Without the library path this repository now sets, numpy, pandas,
pymupdf, duckdb and scikit-learn failed to import with a missing
`libstdc++.so.6`; with it they imported.

Two defects of the previous state were found by building for the first time:

- `ast-index` at its locked revision failed to link (`cannot find -lsqlite3`)
  against the followed Nixpkgs, so neither the shell nor the VM could be built.
  `nix/dev-tools.nix` holds the workaround and names the upstream fix.
- The PyPI `ruff` wheel is a generic Linux executable and did not start on
  NixOS, so `just check` could not pass there. The gate now uses the pinned
  `ruff` from the Nix shell or the guest.

On this workstation the test suite needs a short temporary directory
(`PYTEST_ADDOPTS=--basetemp=$XDG_RUNTIME_DIR/hacklab-pytest`): its default
temporary path is long enough that the development-tool tests reject their
Unix socket paths. The guest and ordinary machines are not affected.

### Not verified

- Bob's license acceptance, IBM login or API key, and any authenticated Bob
  session. Whether Bob activates the linked skills was not observed; the
  location follows IBM's documentation and the 2.1.0 client's skill scanner.
- An editor remote session into the guest.
- The laptop that will be used at the event, its KVM access and the event Wi-Fi.
- The two organizer-provided API keys, the generative model, the official
  benchmark transport, submission contract, event prices and score normalization.
- `nix develop .#with-bob` beyond building its environment.

## 2026-11-11 in a managed cloud environment (not repeated)

The earlier run used Python 3.12 and had no `/nix`, sudo or `/dev/kvm`; it could
evaluate the flake but not build it. Its results below concern harness
behavior that the current test suite still covers, and development-tool
programs that were built there outside Nix.

| Check | Observed result |
| --- | --- |
| `uv sync --locked --offline` | Passed after the first complete online sync on that machine |
| `hacklab solve --task bench/task.example.json --root bench/data` | Returned the synthetic one-line contact with exact `line:1` citation in mock mode |
| Mock practice benchmark | All eight cases executed; 2/8 local rubric passes, zero API calls |
| Benchmark compare, stats and private JSON export | Executed successfully |
| Local submission adapter | Successful JSONL answer and existing-output preservation tested |
| `uv build` | Source distribution and wheel built successfully |
| Native Jev and generative HTTP contract tests | Synthetic HTTP fixtures passed; retries, usage, model IDs and malformed responses covered |
| Git-backed source snapshot | Checked that `.venv`, `.state`, `.env` and `.git` are absent |
| Ichnora, AST Index, Tabula, evidence and tool meter | Real programs exercised on disposable data by `scripts/smoke-dev-tools.py` (`just dev-check`) |
| Development lifecycle | Setup idempotence, unrelated MCP preservation, own-process stop/restart and memory-preserving tool-path refresh passed |

The test suite covers scoped/symlink-safe document access; CSV, PDF, DOCX and
XLSX parsing; ZIP/response bounds; decimal arithmetic; exact citations and
changed input bytes; typed decisions; bounded repair; provider errors, retries,
timeouts and rate-limit delays; unknown usage/cost; metadata privacy and
concurrent tracking; benchmark label isolation and numeric false positives.
The development-tool tests additionally cover scoped external state, disabled
transcript collection, exact enrollment identity, AST exclusions and clean
environment bindings, private config merging for Bob and other clients,
interpreter preservation, startup recovery, process identity and stale sockets.

Mock mode deliberately abstains from the six practice tasks needing inference.
Its two abstention passes are plumbing evidence, **not a small-model accuracy
measurement**. Unit-test HTTP fixtures do not establish real API connectivity.

## Before travelling

On the laptop you will bring, run `bash scripts/vm-smoke`, then
`bash scripts/vm-run`. Set the console password, run `uv sync --locked` and
`just check` once in the persistent guest, and complete Bob's license and
login steps from [docs/vm.md](docs/vm.md#ibm-bob). In an existing Lab VM,
`nix develop` and the same Python commands avoid nested virtualization.
