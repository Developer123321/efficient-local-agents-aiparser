# Existing tools and skills

This project reuses the owner's existing composition rather than replacing it:

| Existing project | Integration |
| --- | --- |
| `bobshell.nix` | Pinned flake input, optional `with-bob` shell / `vm-with-bob` image |
| `unix-agent-skills` | Focused immutable skill links from its pinned source |
| `private-skills-catalog` | Selected development/evidence skills, preserving private ownership |
| `nixlab` / its Lab VM | Run this devShell inside the existing guest; keep its agent profiles and secret manager |
| `coding-agents-nix` / `security-agents-nix` | Existing owner of Codex/OMP/Claude, MCP, managed tools and profile state |
| `workspace.nix` | Keep the existing workspace discovery and project launcher; this repository is an ordinary project checkout |
| `jev-nix` | Preserve its typed-judgment/closed-tool design; this harness adds a competition-specific generator and full per-attempt tracking |

The ten selected skills are goal-workflow, context-management, tool-use, lean-code,
fast-checks, test-freshness, repository-evidence, hypothesis-testing,
workload-measurement and python-task-scripts. Their canonical implementations
remain in their catalogs. `bash scripts/vm-skills` links the selection into
`.agents/skills`; it preserves existing files. Confirm your assistant's discovery
layout and start a new session. Skill links are not proof that an associated
service or CLI is installed.

The new standalone VM installs the portable development/data CLI set in
`nix/tools.nix`. It does not clone personal MCP configuration, authenticated
assistant sessions, browser profiles or host secret state. To retain the full
existing optimized agent toolchain immediately, run this Python project inside
your already configured Lab VM and use its normal managed agent launcher.
Use `agent-toolbox --list` there to inspect the actual available tools. AST,
memory, evidence and browser services remain governed by that composition.

Bob setup:

```sh
nix develop .#with-bob
bob --version
bob --help
# Follow the existing package's license/authentication instructions, then:
bob chat
```

Read the [existing Bob packaging guide](https://github.com/timfewi/bobshell.nix#license-and-authentication-at-runtime)
for IBM terms, authentication and `BOB_API_KEY`. No license is accepted, account
logged in or credits consumed by `uv sync`, `doctor`, the VM build or mock tests.
Bob, Codex and other coding assistants may work on the code. The runtime
competition harness only uses the provided small model and Jev.

The native Jev adapter is intentionally not a direct reuse of `jev-nix`'s SDK
wrapper: all HTTP attempts, including retries and ambiguous errors, must be
visible to this benchmark's shared budget and tracking. Its protocol is checked
against the [official System One contract](https://docs.typesafe.ai/api/system-one.md).
