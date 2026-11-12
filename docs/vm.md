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
git clone https://github.com/timfewi/hacklab-vm.git
cd hacklab-vm
nix develop
just ready
just practice
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

It provides Python 3.15, uv, Ruff, Git/GitHub CLI, Just, ripgrep, fd, jq/yq,
SQLite, DuckDB, PDF extraction, OCR for English and German, Pandoc, tmux, vim
and timing tools, and rootless Podman with a `docker` alias. No GPU or local
model weights are required. Binary wheels that load `libstdc++` or `zlib`, such
as numpy, pandas, pymupdf and duckdb, import without extra setup.

Three older interpreters are present under their versioned names:
`python3.13` is what the submission image runs, `python3.11` is the oldest
version the project supports, and `python3.14` is the escape hatch for a
package without a CPython 3.15 wheel. `export UV_PYTHON=3.13` and a new sync
run the project on that interpreter.

The VM includes a read-only snapshot of the reviewed Git-backed project source.
At first boot a system service copies that snapshot into `~/workspace/hacklab-vm`
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
cd ~/workspace/hacklab-vm
just ready            # Dependency sync, the linked Jev skill, readiness report
just practice         # A rehearsal incident, solved by code without a model
just container-test   # Build the submission image and run it without network
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
loopback out of reach, `just ready`, `just check`, the mock benchmark, the
rehearsal incident, the tests on Python 3.13 and 3.11, a native-wheel import
and `scripts/container-test`. It needs KVM, PyPI and the container image
registries, takes about ten minutes, and leaves the real disk untouched.
`SKIP_CONTAINER_TEST=1 bash scripts/vm-smoke` leaves the image build out.

## Containers

The agent is evaluated in a container without internet access, so the image is
part of what has to work. `scripts/container-test` (`just container-test`)
builds the [Dockerfile](../Dockerfile) and then, with no network at all:

1. generates a rehearsal incident and solves it with the playbooks;
2. starts a stand-in model endpoint and runs the agent against it in the same
   network namespace, which has a loopback interface and nothing else;
3. checks that the agent's namespace cannot reach the internet;
4. checks that the agent runs as an unprivileged user that cannot write to
   its own code.

It uses `docker` when present and `podman` otherwise; `CONTAINER_ENGINE`
selects one explicitly. The guest has rootless Podman: no daemon, and no
membership in a root-equivalent group. On a laptop with Docker Desktop the
same script runs unchanged. Only the build needs the registries and PyPI.

The image installs exactly `uv.lock` on `python:3.13-slim`, contains the
`hacklab` package and `configs/`, and writes its receipts under `/tmp`. Its
`CMD` is a placeholder: set it to the organizers' run contract at the opening
(see the comment in the Dockerfile), and add the starter kit's files and
dependencies to the build. Keys never go into the image; they arrive as
environment variables at run time. Keep `.env` out of the build context:
`.dockerignore` admits only the files the image needs.

## On a NixOS host: `hacklab-vm`

A NixOS machine can carry the guest as an on-demand service instead of running
`scripts/vm-run` from a checkout. Add this repository as a flake input, then:

```nix
{
  imports = [ inputs.hacklab-vm.nixosModules.host ];
  services.hacklab-vm = {
    enable = true;
    operatorName = "alice";     # Existing account that owns and runs the VM
    # memoryMiB = 8192; vcpu = 4; bob.enable = true; stateDirectory = null;
  };
}
```

The module installs a user service that is never started automatically and the
`hacklab-vm` command for that account:

| Command | Effect |
| --- | --- |
| `hacklab-vm start` / `stop` / `restart` | Control the VM; `stop` asks the guest to power off cleanly |
| `hacklab-vm view` | Attach this terminal to the guest console; Ctrl-] detaches and the session keeps running |
| `hacklab-vm status` / `logs` | Service state and the guest's boot console |

Without `follows`, the guest keeps the package set this repository tests. A
host that makes the input follow its own Nixpkgs gets a different guest, for
example another Python patch release. Boot-test that one from a checkout:

```console
runner=$(nix build --no-link --print-out-paths \
  'git+file:///path/to/host#nixosConfigurations.NAME.config.services.hacklab-vm.runner')
HACKATHON_VM_RUNNER=$runner bash scripts/vm-smoke
```

No sudo is involved: QEMU runs as the operator with user-mode networking. Disk,
the per-boot store image and two private control sockets live in
`~/.local/state/hacklab-vm` unless `stateDirectory` names another path; keep it
on persistent storage. The service ends with the operator's login session.

The host gains no network listener at all. `view` uses a Unix socket that only
the operator can open, so reaching the guest from another machine means logging
in to the host first, for example over the host's own Tailscale SSH, and
running `hacklab-vm view` there. The guest is not a Tailnet node.

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
3. Start `bob chat` in `~/workspace/hacklab-vm` and ask it to list its skills.

In that directory Bob loads `AGENTS.md` as workspace rules and reads the
project skills in `.bob/skills`. Bob asks before activating a skill unless you
allow the `skill` permission in its own settings.

Bob is a coding assistant for building the solution. It is not silently used as
a third competition model. IBM account authentication, event credits and terms
remain runtime steps; no key, consent record or IBM payload is committed here.
Consult the existing [`bobshell.nix` instructions](https://github.com/timfewi/bobshell.nix#license-and-authentication-at-runtime).
Do not upload Bob-containing closures or VM images to a public binary cache.

## The linked skill and the NixOS module

`nix/skills.nix` links one skill from its pinned source: TypeSafe's own
`typesafe-ai` skill for building against Jev. Its instructions are not copied
or forked. `HACKATHON_SKILLS_DIR` points to it. The dedicated guest links it
into the seeded project at first boot: `.bob/skills` with Bob,
`.agents/skills` without. Elsewhere, or after a pin update:

```console
bash scripts/vm-skills
```

The script chooses `.bob/skills` when `bob` is installed and `.agents/skills`
otherwise; pass another skill-discovery directory as the first argument when
your assistant documents a different layout. It refreshes its own links and
preserves every other file. Use one location: Bob 2.1.0 scans both and reports
a skill found twice as shadowed. Restart the assistant after linking. Project
conventions for every coding assistant are in [AGENTS.md](../AGENTS.md);
`CLAUDE.md` imports the same file. The competition harness uses its own short
run-time prompts and loads no skill.

For an existing NixOS guest, the optional module installs this workbench without
creating another VM:

```nix
{
  imports = [ inputs.hacklab-vm.nixosModules.default ];
  hackathon.enable = true;
  hackathon.bob.enable = false;
}
```

It adds the tool list of `nix/tools.nix` and the skill link. It does not
configure a container engine, any coding assistant or credentials; those stay
with the machine's own configuration.

## Reproducibility and verification

`flake.lock` fixes Nixpkgs, the Bob packaging and the TypeSafe skill. `uv.lock`
fixes the Python dependency resolution, and the Dockerfile installs from it. Enter the shell before syncing to use
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
