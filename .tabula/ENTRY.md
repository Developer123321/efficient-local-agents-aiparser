# hacklab-vm

This repository provides a small-model agent harness for an incident-response
challenge, a rehearsal scenario and a local development VM. Start with the
[README](../README.md), the [event facts](../docs/hackathon.md) and the
[day plan](../docs/playbook.md).

## Competition runtime

[Task and answer contracts](../src/hacklab/models.py) separate task input from
benchmark labels and accept the usual shape variants of a small model's reply.
The [agent](../src/hacklab/agent.py) first offers a task to registered
[playbooks](../src/hacklab/playbooks.py), code-only solvers that need no model
call, and otherwise runs a bounded loop of typed steps. The
[toolkit](../src/hacklab/toolkit.py) presents one-line tool descriptions,
renders results as lines with evidence IDs and adapts external tools. It draws
on [document, search and table tools](../src/hacklab/tools.py) and on the
[forensic library](../src/hacklab/forensics/__init__.py):
[layered decoding and chunk reassembly](../src/hacklab/forensics/decode.py),
[indicators](../src/hacklab/forensics/iocs.py),
[timestamps and row filters](../src/hacklab/forensics/events.py),
[propagation graphs](../src/hacklab/forensics/graph.py) and
[kill-chain phases](../src/hacklab/forensics/phases.py).

The [evidence ledger](../src/hacklab/evidence.py) gives every shown row an ID
that answers cite; [guards](../src/hacklab/guard.py) flag instruction-like
text, replace secrets and blobs with placeholders and check containment
actions. The [HTTP providers](../src/hacklab/providers.py) count every request
attempt, enforce the task budget and the team's development allowance and can
replay replies from the [development cache](../src/hacklab/cache.py);
[tracking](../src/hacklab/tracking.py) keeps usage and cost unknown when
missing. [Configuration](../src/hacklab/config.py) resolves runtime
credentials and holds the guard switches.

## Measurement and rehearsal

The [practice evaluator](../src/hacklab/benchmark.py) scores exact, set and
action answers and groups failures; it is distinct from official scoring. The
[rehearsal module](../src/hacklab/practice.py) generates a synthetic swarm
incident with reference answers and solves it with playbooks. The
[CLI](../src/hacklab/cli.py) runs tasks, benchmarks, the submission adapter and
analyst helpers; the [stand-in endpoint](../src/hacklab/mockserver.py) serves
plumbing tests.

## Environment and checks

The [Dockerfile](../Dockerfile) builds the unprivileged submission image from
the locked dependencies; [the container test](../scripts/container-test) runs
it without any network. The [flake](../flake.nix) pins the workbench, the Bob
packaging and the TypeSafe skill; [the tool list](../nix/tools.nix) selects
Python 3.15 with older interpreters beside it. [VM configuration](../nix/vm.nix)
seeds a writable Git-backed checkout at first boot and provides rootless
Podman. [The runner](../scripts/vm-run) starts the guest on its console, the
only way in: no SSH daemon, no forwarded port; [the host module](../nix/host.nix)
wraps it as an on-demand user service with the
[`hacklab-vm` command](../scripts/hacklab-vm.sh); [the smoke test](../scripts/vm-smoke)
boots a disposable guest and runs [the guest checks](../scripts/guest-check).
The [Justfile](../Justfile) selects explicit workflows; [the fast gate](../scripts/check)
runs Python formatting, lint and tests. [Verification](../VERIFICATION.md)
distinguishes what ran from model, Bob and organizer checks that did not.
