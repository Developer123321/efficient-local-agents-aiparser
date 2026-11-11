# Verification record

Checked on **2026-11-11** in the managed Linux development environment.

| Check | Observed result |
| --- | --- |
| `uv sync --locked` | Passed with Python 3.12.14; 25-package resolution including this project |
| `uv sync --locked --offline` | Passed after the first complete online sync on this machine |
| `bash scripts/check` | Passed: Ruff formatting/lint and **83 Python tests** |
| `hacklab doctor` | Passed local readiness inspection, no network requests |
| `hacklab solve --task bench/task.example.json --root bench/data` | Returned the synthetic one-line contact with exact `line:1` citation in mock mode |
| Mock practice benchmark | All eight cases executed; 2/8 local rubric passes, zero API calls |
| Benchmark compare, stats and private JSON export | Executed successfully |
| Local submission adapter | Successful JSONL answer and existing-output preservation tested |
| `uv build` | Source distribution and wheel built successfully |
| Native Jev and generative HTTP contract tests | Synthetic HTTP fixtures passed; retries, usage, model IDs and malformed responses covered |
| Nix flake evaluation | `nix flake check --no-build --no-write-lock-file` passed |
| Nix formatting | All Nix source passed the pinned Nixpkgs `nixfmt` formatter |
| VM scripts | Bash syntax and Shellcheck 0.11.0 passed |
| Selected skill links | Preservation and idempotence checked with synthetic directories |
| Git-backed source snapshot | Checked that `.venv`, `.state`, `.env` and `.git` are absent |

The test suite covers scoped/symlink-safe document access; CSV, PDF, DOCX and
XLSX parsing; ZIP/response bounds; decimal arithmetic; exact citations and
changed input bytes; typed decisions; bounded repair; provider errors, retries,
timeouts and rate-limit delays; unknown usage/cost; metadata privacy and
concurrent tracking; benchmark label isolation and numeric false positives.

Mock mode deliberately abstains from the six practice tasks needing inference.
Its two abstention passes are plumbing evidence, **not a small-model accuracy
measurement**. Unit-test HTTP fixtures do not establish real API connectivity.

## Nix execution conditions

The cloud did not have an installed `/nix`, sudo or `/dev/kvm`. Nix 2.35.6 was
obtained from its official binary distribution, checked against its published
installer hash and run from private scratch storage. Its custom local store
allowed actual flake locking and evaluation without changing the host system.
Nixpkgs was kept at the revision already used by the existing Bob packaging;
Bob and both skill catalogs have real Git revisions and NAR hashes in flake.lock.

An actual contract build was attempted. The environment failed while setting up
the builder with `cannot set domain name: Operation not permitted`. Mount-based
chroot setup was also unavailable. Therefore **the VM image has not been built
or booted here**, and KVM, guest networking and first-login behavior remain
target-machine checks. Evaluation establishes typed configuration and build
derivations, not an executed guest.

## Target-machine completion

On the Security Laptop, run `nix build .#vm`, then `bash scripts/vm-run`. Set the
console password, enter the seeded project, run `uv sync --locked`, `just check`
and the example task. In an existing Lab VM, `nix develop` and the same Python
commands avoid nested virtualization. Prewarm the actual machine before travel.

Bob's license acceptance, IBM authentication/credits, personal MCP services,
and the two organizer-provided API keys were not exercised. The generative
model, official benchmark transport, submission contract, event prices and
score normalization must be confirmed at the opening. No GPU, external model
credentials or live provider calls were used for these checks.
