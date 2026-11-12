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
ssh -p 2222 hackathon@127.0.0.1     # From a second host terminal
```

`vm-run` builds the guest, hands it one SSH public key and boots it on the
serial console. At every boot it authorizes the first of `~/.ssh/id_ed25519.pub`,
`id_ecdsa.pub` and `id_rsa.pub`; set `HACKATHON_VM_SSH_PUBKEY=/path/key.pub` to
choose another file. Only that public key line reaches the guest, through a QEMU
firmware entry that systemd stores on tmpfs. The private key stays on the host,
and no host directory is mounted. An editor on the host can work in the guest
over the same SSH connection. `nix-ld` is enabled for the prebuilt binaries
such editors install; no editor remote session was tested.

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
before leaving the console unattended. Then, on the console or over SSH:

```console
passwd
cd ~/workspace/python-vm
just ready          # Dependency sync, development memory, code index, MCP, skills
just benchmark
```

SSH accepts public keys and rejects password/root login. The host listener binds
only `127.0.0.1:2222`; there is no LAN listener. QEMU user-mode NAT allows the
guest to reach the model APIs, and the guest's firewall rejects new connections
to the host's loopback services. The host home, workspace, SSH agent and device
files are not mounted into the guest. Reach a service inside the guest with an
SSH tunnel, for example `ssh -p 2222 -L 8000:127.0.0.1:8000 hackathon@127.0.0.1`.

For later updates, copy reviewed, versioned project source from the host:

```console
bash scripts/vm-copy
ssh -p 2222 hackathon@127.0.0.1
cd ~/workspace/python-vm
uv sync --locked
just doctor
just benchmark
```

`vm-copy` copies Git-tracked files from the current checkout, including staged
source changes. Ignored runtime data and secrets are excluded. It keeps normal
SSH host-key checks and does not copy `.git`. Copy private datasets separately
with `scp -P 2222`. Enter event API keys inside the guest or resolve them using
your existing runtime secret manager.
If an update adds new source files, review and `git add` those exact paths in
the guest before using Nix there; never stage private runtime data.

Shut the guest down with `sudo poweroff`. Its disk remains at
`${XDG_STATE_HOME:-$HOME/.local/state}/efficient-local-agents/vm/hackathon.qcow2`.
The `run/` directory beside it holds the Nix store image that the runner
rewrites at every start (the system closure is about 2 GiB); it is kept off
`/tmp`, which is often RAM-backed. `vm-run` holds an exclusive lock to reject a second runner against
that disk. To move a configured guest, shut it down first and copy that disk
privately; it may contain credentials, documents and traces. Never commit or
publish it.

`bash scripts/vm-smoke` checks a build end to end on the current machine. It
boots a disposable disk with a throwaway key, then runs the dependency sync,
`just check`, the mock benchmark and a native-wheel import inside the guest. It
needs KVM, a free port 2222 and PyPI access, and leaves the real disk untouched.

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
| `python-vm view` | Open the guest's persistent `tmux` session in `~/workspace/python-vm` |
| `python-vm ssh [ARGUMENTS...]` | Run SSH against the guest, for example `python-vm ssh -L 8000:127.0.0.1:8000` |
| `python-vm status` / `logs` | Service state and the guest's boot console |

Without `follows`, the guest keeps the package set this repository tests. A
host that makes the input follow its own Nixpkgs gets a different guest, for
example another Python patch release. Boot-test that one from a checkout:

```console
runner=$(nix build --no-link --print-out-paths \
  'git+file:///path/to/host#nixosConfigurations.NAME.config.services.python-vm.runner')
HACKATHON_VM_RUNNER=$runner bash scripts/vm-smoke
```

No sudo is involved: QEMU runs as the operator with user-mode networking. The
first start creates an SSH key used only for this guest, so no personal key is
needed. Disk, key and the per-boot store image live in
`~/.local/state/python-vm` unless `stateDirectory` names another path; keep it
on persistent storage. The service ends with the operator's login session.

The guest reaches the Internet through the host's ordinary outbound path and
is not reachable from the network the host is connected to: the only listener
is `127.0.0.1:2222` on the host. The guest's firewall also rejects new
connections to the host's own loopback services, which QEMU's user network
would otherwise expose at `10.0.2.2`.

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
