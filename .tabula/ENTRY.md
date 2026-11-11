# python-vm

This repository provides a local development VM and a small-model competition
harness. Start with the [README](../README.md) and
[verified event details](../docs/hackathon.md).

## Competition runtime

[Task and answer contracts](../src/hacklab/models.py) separate task input from
benchmark labels. The [agent loop](../src/hacklab/agent.py) dispatches bounded
[document and arithmetic tools](../src/hacklab/tools.py). The
[HTTP providers](../src/hacklab/providers.py) count every actual request attempt;
[tracking](../src/hacklab/tracking.py) keeps usage and cost unknown when missing.
The [practice evaluator](../src/hacklab/benchmark.py) is distinct from official
scoring. [Configuration](../src/hacklab/config.py) resolves runtime credentials.

## Development helpers

The [development tool controller](../src/hacklab/devtools.py) prepares an isolated
project memory service, an explicitly refreshed AST index and local MCP clients.
These helpers preserve development decisions and support code navigation. The
competition loop does not load their persistent state. See
[tool integration](../docs/tools.md) for scope, lifecycle and verification.

## Environment and checks

The [flake](../flake.nix) pins the workbench, Bob packaging and development tools.
[VM configuration](../nix/vm.nix) seeds a writable Git-backed checkout at first
login. The [Justfile](../Justfile) selects explicit workflows;
[the fast gate](../scripts/check) runs Python formatting, lint and tests.
[Verification](../VERIFICATION.md) distinguishes checks that ran from target-only
VM boot and authenticated-model checks.
