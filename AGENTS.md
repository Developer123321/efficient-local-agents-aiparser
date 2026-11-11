# Working in python-vm

Read README.md, docs/architecture.md and docs/hackathon.md first. This repository
owns a portable development VM and an inspectable starter harness, not the host
machine configuration. Keep existing host and Lab VM policy intact.

- Python: `uv sync --locked`, then `just check`. Nix: `nix flake check` and the
  explicit VM checks in docs/vm.md. Report checks actually executed and blockers.
- Use code for control flow, arithmetic, parsing and validation. Keep model
  decisions small and typed. Jev is a native System One provider, never a chat model.
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
