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
uv sync --frozen
just doctor
just benchmark
```

This avoids a second guest and does not require nested virtualization. The
Security Laptop's physical host remains free of additional coding-agent
profiles. No existing host configuration, VM disk, VPN enrollment or secret
provider is changed by this repository.

## Dedicated QEMU VM

On an x86_64 Linux machine with Nix flakes enabled:

```console
nix build .#vm
bash scripts/vm-run
```

The default guest has 4 vCPUs, 8 GiB RAM and a 40 GiB sparse persistent disk.
It provides Python 3.12, uv, Ruff, Git/GitHub CLI, Just, ripgrep, fd, jq/yq,
SQLite, DuckDB, PDF extraction, OCR, Pandoc, tmux and timing tools. No GPU or
local model weights are required. Keep at least 4 GiB RAM for the host; build
and dependency caches need additional disk space beyond the sparse guest disk.

Ichnora, AST Index, agent-evidence, agent-tool-meter and Tabula are also included
as pinned packages. After syncing the Python project, run `just dev-setup` to
prepare this checkout's memory service, AST snapshot and local MCP clients. The
same command works in the existing Lab VM after entering this project's shell.

The VM includes a read-only snapshot of the reviewed Git-backed project source.
At first console login it copies that snapshot into `~/workspace/python-vm`
only if that directory does not exist. Existing projects are preserved. Ignored
credentials, traces, datasets and `.venv` are excluded from the source snapshot.
The fresh copy is initialized as a local Git repository with the pristine seed
tracked, without an origin or historical commits. This retains Git filtering
for later Nix commands before any runtime secrets are added.
Build through the normal Git-backed flake reference (`nix build .#vm`), not a
`path:` reference that bypasses Git filtering.

The console logs into the ordinary `hackathon` account, initially with an empty
local password so its first `passwd` works. Root is locked. Set the password
before leaving the console unattended. On first login:

```console
passwd
mkdir -p ~/.ssh
chmod 700 ~/.ssh
# Paste your host's PUBLIC SSH key, as one line, into this file:
nano ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
# The project is already available:
cd ~/workspace/python-vm
uv sync --frozen
just doctor
just benchmark
```

Keep the private key on the host. SSH accepts public keys and rejects password/root
login. The host listener binds only `127.0.0.1:2222`; there is no LAN listener.
QEMU user-mode NAT allows the guest to reach the model APIs. The host home,
workspace, SSH agent and device files are not mounted into the guest.

For later updates, copy reviewed, versioned project source from the host and
connect after installing the public key:

```console
bash scripts/vm-copy
ssh -p 2222 hackathon@127.0.0.1
cd ~/workspace/python-vm
uv sync --frozen
just doctor
just benchmark
```

`vm-copy` copies Git-tracked files from the current checkout, including staged
source changes. Ignored runtime data and secrets are excluded. It keeps normal
SSH host-key checks and does not copy `.git`. Copy private datasets separately
when needed. Enter event API keys inside the guest or resolve them using your
existing runtime secret manager.
If an update adds new source files, review and `git add` those exact paths in
the guest before using Nix there; never stage private runtime data.

Shut the guest down with `sudo poweroff`. Its disk remains at
`${XDG_STATE_HOME:-$HOME/.local/state}/efficient-local-agents/vm/hackathon.qcow2`.
`vm-run` holds an exclusive lock to reject a second runner against that disk.
To move a configured guest,
shut it down first and copy that disk privately; it may contain credentials,
documents and traces. Never commit or publish it.

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
Use the separate shell or VM variant:

```console
nix develop .#with-bob
bob --version
# Or:
bash scripts/vm-run --bob
```

Bob is a coding assistant for building the solution. It is not silently used as
a third competition model. IBM account authentication, event credits and terms
remain runtime steps; no key, consent record or IBM payload is committed here.
Consult the existing [`bobshell.nix` instructions](https://github.com/timfewi/bobshell.nix#license-and-authentication-at-runtime).
Do not upload Bob-containing closures or VM images to a public binary cache.
The default VM excludes Bob.

## Your tools and skills

`nix/skills.nix` selects twelve relevant skills directly from the pinned
`unix-agent-skills` and `private-skills-catalog` sources. It creates immutable
links, without copying or forking their instructions. `HACKATHON_SKILLS_DIR`
points to the selection. To expose it to an assistant that supports the
`.agents/skills` convention:

```console
bash scripts/vm-skills
```

Existing files are preserved. You may pass a different skill-discovery
directory as the first argument when your assistant documents another layout.
Restart the assistant after linking. Merely exposing a directory does not
prove every client has loaded it. Skills help the development assistant;
the competition harness uses its own short, bounded runtime prompts.

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

The development run in this cloud environment successfully evaluated the full
flake, both VM variants and both shells with Nix 2.35.6, and checked formatting
with the pinned Nixpkgs formatter. This cloud has no `/dev/kvm`, no installed
`/nix` and no mount permission for a local chroot. An attempted contract build
was blocked while setting up its build environment with
`cannot set domain name: Operation not permitted`. A VM build/boot has not
been established here.

Evaluation checks option types and derivations. A VM build still does not
demonstrate a successful boot, guest networking, target-hardware KVM, IBM login
or the organizer's unknown benchmark contract. Record those checks on the
actual laptop separately. The event adapters and offline practice benchmarks
are described in the project README.
