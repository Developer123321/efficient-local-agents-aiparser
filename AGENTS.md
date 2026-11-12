# Working in python-vm

Read README.md, docs/architecture.md and docs/hackathon.md first. This repository
owns a portable development VM and an inspectable starter harness, not the host
machine configuration. Keep existing host and Lab VM policy intact.

- Python: `uv sync --locked`, then `just check`. Nix: `nix flake check` and the
  explicit VM checks in docs/vm.md. Report checks actually executed and blockers.
- Use code for control flow, arithmetic, parsing and validation. Keep model
  decisions small and typed. Jev is a native System One provider, never a chat model.
  Use the linked `typesafe-ai` skill when designing Jev state, questions or
  thresholds, and keep those constants together in one module.
- Bob and other coding assistants build the solution; only the two models
  provided by the organizers may make runtime competition decisions.
- Keep benchmark reference answers out of model input, tool results and memory.
  Keep training/development fixtures separate from organizer and held-out data.
- Track every provider request attempt, including retries, errors and Jev calls.
  Missing usage or prices means unknown cost. Never infer tokens from text length.
- Quotes validate provenance, not semantic truth. Test conflict resolution and
  constraint compliance with held-out examples before claiming accuracy.
- Secrets, private documents, model bodies, VM disks and runtime state stay out
  of Git and the Nix store. Do not enable body logging. Runtime logs are local.
- General reusable skills remain in the owner's canonical skill catalogs;
  select or link them, do not duplicate their implementations here.
- Prefer one focused improvement, run the relevant checks and compare actual
  accuracy/cost on the same fixtures. No claim of optimality or winning guarantees.

Source and CLI text are English. User-facing handoff may be German.

## Local development helpers

`just dev-setup` explicitly starts project-scoped Ichnora memory, indexes source
with AST Index and writes local MCP configuration. `just dev-status` checks the
actual service and index. Reindex after source changes with `just index`.

Before recalling memory, verify `memory_status.scope` matches this checkout.
Capture durable decisions, evidence and task checkpoints; check dependencies
before trusting an older claim. Do not collect transcripts or store benchmark
reference answers, evaluation inputs, credentials or private output in memory.
Memory is for development continuity; `hacklab solve/benchmark/submit` never
loads it. Before structural navigation, inspect AST `code_explore` status and
use paths relative to its returned root. Queries do not refresh the index.
Use `agent-evidence` for bounded, digest-bound reads; `agent-tool-meter` measures
CLI time and output bytes, not model tokens. Keep its logs outside Git.
