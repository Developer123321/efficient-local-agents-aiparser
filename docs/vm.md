# VM and laptop setup

The same locked Python project runs in an existing Lab VM, in a local Nix
development shell, or in the dedicated NixOS QEMU VM. The provided Nix outputs
target **x86_64 Linux**, matching your `bobshell.nix` package and Security Laptop.
They do not claim native Windows, macOS or ARM support.

## Fastest path in your existing Lab VM

Your `nixlab` configuration already gives the Lab VM its own Codex, OMP and
Claude Code profiles through `security-agents-nix`. Keep using those launchers,
tools, credentials and isolated profile state. Clone this project into the
guest's normal workspace and enter its Python shell:

```console
git clone https://github.com/timfewi/python-vm.git
cd python-vm
nix develop
uv sync --locked
just doctor
just benchmark
```

This avoids a second guest and does not require nested virtualization. The
Security Laptop's physical host remains free of additional coding-agent
profiles. No existing host configuration, VM disk, VPN enrollment or secret
provider is changed by this repository.

## Dedicated QEMU VM

On an x86_64 Linux machine with Nix flakes and KVM:

```console
bash scripts/vm-run                 # Includes IBM Bob Shell; --no-bob leaves it out
```

`vm-run` builds the guest and opens its console in the same terminal, logged in
as `hackathon`. That console is the only way in: **the guest runs no SSH daemon,
no port is forwarded and nothing is mounted from the host.** Start `tmux` in the
console for several windows, and type `fit` after resizing the terminal window,
because a serial console cannot report its size.

The default guest has 4 vCPUs, 8 GiB RAM and a 40 GiB sparse persistent disk.
On a smaller laptop start it with, for example, `--memory 6144 --cpus 2`. Keep
at least 4 GiB RAM for the host.

It provides Python 3.15 (with `python3.14` as a fallback interpreter), uv, Ruff,
Git/GitHub CLI, Just, ripgrep, fd, jq/yq, SQLite, DuckDB, PDF extraction, OCR
for English and German, Pandoc, tmux, vim and timing tools. No GPU or local
model weights are required. Binary wheels that load `libstdc++` or `zlib`, such
as numpy, pandas, pymupdf and duckdb, import without extra setup. If a package
has no CPython 3.15 wheel yet, run `export UV_PYTHON=3.14` and sync again.

Ichnora, AST Index, agent-evidence, agent-tool-meter and Tabula are also included
as pinned packages. After syncing the Python project, run `just dev-setup` to
prepare this checkout's memory service, AST snapshot and local MCP clients. The
same command works in the existing Lab VM after entering this project's shell.

The VM includes a read-only snapshot of the reviewed Git-backed project source.
At first boot a system service copies that snapshot into `~/workspace/python-vm`
only if that directory does not exist. Existing projects are preserved. Ignored
credentials, traces, datasets and `.venv` are excluded from the source snapshot.
The fresh copy is a local Git repository with the pristine seed staged and
`origin` set to this project's GitHub URL, without historical commits. This
retains Git filtering for later Nix commands before any runtime secrets are
added. To continue on the shared history, authenticate and run
`git fetch origin && git reset origin/main`.
Build through the normal Git-backed flake reference (`nix build .#vm`), not a
`path:` reference that bypasses Git filtering.

The console logs into the ordinary `hackathon` account, initially with an empty
local password so its first `passwd` works. Root is locked. Set the password
before leaving the console unattended; `sudo` needs it. Then:

```console
passwd
cd ~/workspace/python-vm
just ready          # Dependency sync, development memory, code index, MCP, skills
just benchmark
```

QEMU user-mode NAT lets the guest reach the model APIs. Nothing reaches the
guest: the host opens no listener for it, and the guest's firewall rejects new
connections to the host's loopback services. The host home, workspace, SSH
agent and device files are not mounted into the guest.

Move work in and out through the network, as the guest sees it: `git` against
the shared repository, downloads with `curl`, or `wormhole receive` for a file
sent from another machine with Magic Wormhole. Enter event API keys inside the
guest.

Shut the guest down with `sudo poweroff`. Its disk remains at
`${XDG_STATE_HOME:-$HOME/.local/state}/efficient-local-agents/vm/hackathon.qcow2`.
The `run/` directory beside it holds the Nix store image that the runner
rewrites at every start (the system closure is about 2 GiB); it is kept off
`/tmp`, which is often RAM-backed. `vm-run` holds an exclusive lock to reject a second runner against
that disk. To move a configured guest, shut it down first and copy that disk
privately; it may contain credentials, documents and traces. Never commit or
publish it.

`bash scripts/vm-smoke` checks a build end to end on the current machine. It
boots a disposable disk and drives the guest console to run
`scripts/guest-check` there: no SSH daemon or network listener, the host's
loopback out of reach, `just ready`, `just check`, the mock benchmark and a
native-wheel import. It needs KVM and PyPI access, and leaves the real disk
untouched.

## On a NixOS host: `python-vm`

A NixOS machine can carry the guest as an on-demand service instead of running
`scripts/vm-run` from a checkout. Add this repository as a flake input, then:

```nix
{
  imports = [ inputs.python-vm.nixosModules.host ];
  services.python-vm = {
    enable = true;
    operatorName = "alice";     # Existing account that owns and runs the VM
    # memoryMiB = 8192; vcpu = 4; bob.enable = true; stateDirectory = null;
  };
}
```

The module installs a user service that is never started automatically and the
`python-vm` command for that account:

| Command | Effect |
| --- | --- |
| `python-vm start` / `stop` / `restart` | Control the VM; `stop` asks the guest to power off cleanly |
| `python-vm view` | Attach this terminal to the guest console; Ctrl-] detaches and the session keeps running |
| `python-vm status` / `logs` | Service state and the guest's boot console |

Without `follows`, the guest keeps the package set this repository tests. A
host that makes the input follow its own Nixpkgs gets a different guest, for
example another Python patch release. Boot-test that one from a checkout:

```console
runner=$(nix build --no-link --print-out-paths \
  'git+file:///path/to/host#nixosConfigurations.NAME.config.services.python-vm.runner')
HACKATHON_VM_RUNNER=$runner bash scripts/vm-smoke
```

No sudo is involved: QEMU runs as the operator with user-mode networking. Disk,
the per-boot store image and two private control sockets live in
`~/.local/state/python-vm` unless `stateDirectory` names another path; keep it
on persistent storage. The service ends with the operator's login session.

The host gains no network listener at all. `view` uses a Unix socket that only
the operator can open, so reaching the guest from another machine means logging
in to the host first, for example over the host's own Tailscale SSH, and
running `python-vm view` there. The guest is not a Tailnet node.

## KVM and nested guests

`vm-run` requires a readable/writable `/dev/kvm` for normal operation. If you
run it inside your existing Lab VM, the outer hypervisor must explicitly expose
hardware virtualization and provide that device. This repository does not
change BIOS, kernel modules or the outer VM's device boundary.

Without KVM, prefer `nix develop` directly in the existing Lab VM. For a slow
software-emulation fallback, use `bash scripts/vm-run --tcg`. On a physical
host with KVM, use the ordinary command. A missing nested KVM device does not
affect the agent's API-only Python runtime.

## IBM Bob

Your existing private packaging is consumed at the revision in `flake.lock`.
`scripts/vm-run` starts the guest that contains it; the Nix shell has a
separate variant:

```console
bash scripts/vm-run             # Guest with Bob Shell
bash scripts/vm-run --no-bob    # Guest without the unfree package
nix develop .#with-bob          # Shell with Bob on an existing machine
bob --version
```

Bob needs three runtime steps that no build performs. Do them once in the
guest before the event; the guest disk keeps the result:

1. Read and accept IBM's terms: `bob --accept-license chat`.
2. Authenticate. The guest has no browser, so IBM's documented route for such
   environments applies: create an API key with scope Inference in the Bob web
   portal and export it as `BOB_API_KEY`, for example in the guest's
   `~/.bashrc`. Browser login from this headless guest is untested.
3. Start `bob chat` in `~/workspace/python-vm` and ask it to list its skills.

In that directory Bob loads `AGENTS.md` as workspace rules, reads the project
skills in `.bob/skills` and, after `just dev-setup`, the MCP servers in
`.bob/mcp.json`. Bob asks before activating a skill unless you allow the
`skill` permission in its own settings.

Bob is a coding assistant for building the solution. It is not silently used as
a third competition model. IBM account authentication, event credits and terms
remain runtime steps; no key, consent record or IBM payload is committed here.
Consult the existing [`bobshell.nix` instructions](https://github.com/timfewi/bobshell.nix#license-and-authentication-at-runtime).
Do not upload Bob-containing closures or VM images to a public binary cache.

## Your tools and skills

`nix/skills.nix` selects twelve skills directly from the pinned
`unix-agent-skills` and `private-skills-catalog` sources, plus TypeSafe's own
`typesafe-ai` skill for building against Jev. It creates immutable links,
without copying or forking their instructions. `HACKATHON_SKILLS_DIR` points to
the selection. The dedicated guest links it into the seeded project at first
boot: `.bob/skills` with Bob, `.agents/skills` without. Elsewhere, or after a
pin update:

```console
bash scripts/vm-skills
```

The script chooses `.bob/skills` when `bob` is installed and `.agents/skills`
otherwise; pass another skill-discovery directory as the first argument when
your assistant documents a different layout. It refreshes its own links and
preserves every other file. Use one location: Bob 2.1.0 scans both and reports
a skill found twice as shadowed. Restart the assistant after linking. Merely
exposing a directory does not prove every client has loaded it. Skills help
the development assistant; the competition harness uses its own short, bounded
runtime prompts.

For an existing NixOS guest, the optional module installs this workbench without
creating another VM:

```nix
{
  imports = [ inputs.python-vm.nixosModules.default ];
  hackathon.enable = true;
  hackathon.bob.enable = false;
}
```

The module does not replace the managed `coding-agents-nix` composition or copy
its credentials. Keep that existing provider as the owner of Codex, OMP,
Claude Code, MCP configuration and security-tool selections.

An existing consumer flake can combine the Python shell with its **already
pinned** optimized Coding Agents tools. For example, Nixlab's provider is
`inputs.security-agents.inputs.coding-agents`:

```nix
devShells.x86_64-linux.hackathon =
  import (inputs.python-vm + "/nix/coding-tools-shell.nix") {
    workbench = inputs.python-vm;
    coding-agents = inputs.security-agents.inputs.coding-agents;
  };
```

This reuses the provider's `lib.x86_64-linux.toolPackages` selection, including
its configured Project Check, Tabula, evidence and tool measurement tools. It
does not create another agent deployment. Existing managed agent launchers
remain available from the Lab VM's system PATH. This project prepares its own
scoped Ichnora and AST state through `just dev-setup`; existing managed services
keep their own configuration. Scopeglass and secured Research still need their
existing explicit service and host bindings. This optional consumer composition
must be evaluated against that consumer's retained pins before use.

## Reproducibility and verification

`flake.lock` fixes Nixpkgs, Bob, the development tools and both skill catalogs. `uv.lock`
fixes the Python dependency resolution. Enter the shell before syncing to use
the Nix-provided Python interpreter. Download dependencies and run the demo on
the target machine before travelling; the first build and first `uv sync` need
Internet access, as do the event's model APIs.

Useful local checks:

```console
nix flake check --no-build
nix build .#checks.x86_64-linux.vm-contract
nix build .#vm
```

[VERIFICATION.md](../VERIFICATION.md) records the build, the boot and the guest
checks that actually ran, and what remains untested. Evaluation checks option
types and derivations; `scripts/vm-smoke` is the check that boots the guest.
Neither demonstrates IBM login, the event network or the organizer's unknown
benchmark contract. Repeat the smoke test on the laptop you will bring.
