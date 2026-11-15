# Publication audit, 15 November 2026

The source can be shared as an experimental hackathon project with the
limitations below. It is not ready for operational incident response. No real
credential was identified in the reviewed Git content. Seven behavioral gaps
remain open; no production fix or live-model measurement is part of this
publication change.

## Scope and evidence

The runtime source reviewed was `99a7254179f4e1a0e2ed11240f42612c924ecfc9`.
The checkout was initially clean on `main`. The inspection covered the 106
tracked paths, all 73 commits reachable from local and remote refs, the three
remote branches, the open research PR's description, commit author identities
and four historical dashboard screenshots. There were no tags or submodules;
GitHub reported no Actions runs and no Pages site. Ignored `.env`, runtime
state, datasets, virtual environments and VM disks are outside the published
tree and were not imported into this audit.

The publication changes add the MIT notice and package metadata, carry the
notice into the container, update the repository name and guest remote,
ignore `.env.*` while retaining `.env.example`, and preserve the existing
CLI, Python package and VM/service names. The four synthetic characterization
test files were reused from the existing audit branch and rerun against the
current runtime source. Agent prompts, tools, solvers and live settings are
unchanged. No Arena call, paid model request or live mutation was made.

AST Index returned a computation-budget error, so decision-critical paths
were read directly. No current Tabula card exists. Project memory was out of
scope. These are evidence limits, not evidence that missing relationships
are safe.

## Publication privacy and licensing

- The tracked privacy scan completed with 74 matches: 68 fixture addresses,
  four synthetic guest/example home paths and two synthetic JWTs. Context
  review did not identify real personal records or credentials in those
  matches.
  Repeating it on the prepared publication tree completed with 89 matches:
  the same four example home paths and two JWTs, plus 83 fixture addresses.
  All 15 added address matches belong to the reused synthetic audit tests.
- Gitleaks over all refs produced five historical matches: two occurrences
  of the same fixed `CRED` fixture in preflight material, a deliberately
  invalid private-key fixture and the two synthetic JWTs. None was identified
  as a working credential. No credential was tested against a service.
- Historical private-address matches were test data, private-range parsing
  examples and QEMU networking. Commit identities use GitHub no-reply
  addresses. The four screenshots show development statistics and synthetic
  examples, without an identified secret or private record.
- The audit feature branch contains force-added `.state/break-it/` preflight
  documents and scripts. Deleted handoff documents and old dashboard source
  also remain in history. Making the existing repository public exposes these
  refs and historical records too; `.gitignore` does not remove them. No
  history rewrite, branch deletion or cleanup of another contributor's work
  is included.
- The owner selected MIT for the repository's contributions. The six direct
  Python runtime dependencies declare MIT or BSD-3-Clause in their installed
  metadata. Dependencies and fetched guidance retain their own licenses;
  this is not a complete transitive copyright/provenance audit. The owner
  must have authority to release the team's contributions. No model weights,
  organizer dataset, private input repository or IBM Bob binary is relicensed.

The Nix flake depends on the private `vm-cli` and `bobshell.nix` repositories.
A public clone therefore cannot reproduce that environment without access.
Their visibility is outside this change. The Python setup and Dockerfile are
the public setup paths; successful local Nix checks would not establish
anonymous access to those inputs.

## Open behavioral findings

All seven findings were reproduced with synthetic fixtures against the
current source. The 21 passing cases deliberately assert the observed unsafe
behavior and its controls. They prove the named local code paths, not that
Gemma can be persuaded to produce a particular reply, or an Arena attack hit.

| ID | Exposure and consequence | Enforcement point and reproduction |
| --- | --- | --- |
| A1, high | With the live profile, appending an acknowledgement request to mail from a verified sender can send a message during an explicitly read-only inbox task. A scripted reader can also invent a sender reply. Recipient validation does not establish action authority. | `playbooks.ACTS`, `inbox`, `arena_run.hands_over_actions`; `test_attack_audit_authority.py` (`inbox_path`, `sender_reply`). |
| A2, medium | An attacker-controlled multipart marker groups a clean board post with a hostile one. A reader naming only the hostile post can make code list both. | Board grouping and verdict expansion in `playbooks.injected_posts`; authority tests (`board_part_marker`). |
| A3, medium | A fabricated answer after a blank line, a list entry after an instruction, or an analyst-looking claim in a scanned transcript becomes an indicator and changes the answer. | `playbooks._listed`, `scanned_indicators`; `test_attack_audit_outbound.py` (`transcript_claim`). |
| A4, enforcement gap | A hostile reader can return a clean agent with an unrelated valid fact ID. Provenance membership alone accepts the unsupported answer. No carrier-to-model persuasion was measured. | `playbooks.brief`, `dispatch`; `test_attack_audit_facts.py` (`brief_accepts`). |
| A5, optional loop | Moving an executable URL from an object value to its key preserves it in the serialized answer, including an addressless refusal. The event solver's separate guard rejects it. | `arena_run.defang`, `to_arena`; outbound tests (`url_dictionary_keys`). |
| A6, high, optional loop | Copying a visible placeholder into an answer restores a synthetic credential that was hidden from the model. No output disclosure check stops that path. This is not a live-solver disclosure reproduction. | `agent._finalize`, `Compactor.expand`; outbound tests (`expands_hidden_secret`). |
| A7, robustness | The HTTP client correctly sends a mutation once after a lost reply, but the callable boundary permits a second identical invocation and a second side effect. Attacker control over the transport was not established. | `arena_tools`, `Toolbox.call`; `test_attack_audit_state.py` (`lost_reply_leaves`). |

The detail-only log controls leave structured facts, answers and containment
calls unchanged. Other controls reject an unknown inbox sender, discard
invented board IDs, defang URL values, and block an uninterrupted fabricated
answer paragraph. These controls do not prove general injection resistance.

The production source locations are
[playbooks.py](../src/hacklab/event/playbooks.py),
[arena_run.py](../src/hacklab/event/arena_run.py),
[toolkit.py](../src/hacklab/toolkit.py),
[agent.py](../src/hacklab/agent.py) and [guard.py](../src/hacklab/guard.py).
The [journal](../JOURNAL.md) separately records the failed final: path-bound
solvers with the loop disabled did not generalize to a different workspace.

## Reproduce

Use the installed project environment, without real keys or endpoints:

```sh
uv run --locked --offline python -m pytest -q tests/test_attack_audit_authority.py tests/test_attack_audit_facts.py tests/test_attack_audit_outbound.py tests/test_attack_audit_state.py
bash scripts/check
```

The test suite removes credential, endpoint, cache and trace overrides and
disables `.env` loading. The recording clients do not send mail or make
network requests. A future repair must change these characterizations into
failing-before, passing-after security regressions, preserving their controls.

## Checks and limits

| Check | Result |
| --- | --- |
| Existing security characterizations in a disposable detached worktree at `99a7254` | 21 passed on Python 3.15.0. Unsafe behavior remains reproduced. |
| `agent-security-scan --offline ... source` on an export of tracked `src/hacklab` | Exit 0; 28 Python files, two applicable rules, no findings or parser errors. Rule digest `5fe096256eecba611326f755aba9658a0efbe334391647abadbec1d5dea7acdd`. Narrow static coverage missed the behavioral gaps. |
| `agent-security-scan --offline ... privacy . --tracked` | Exit 1, complete; 74 matches in the original tree, 89 in the prepared tree, reviewed as above. Untracked files, archive contents and history are excluded by this stage. |
| Gitleaks 8.30.1: `gitleaks git . --log-opts=--all --redact=100 --ignore-gitleaks-allow` | Exit 1; all 73 initially reachable commits scanned, five fixture matches reviewed as above. |
| `osv-scanner scan source --lockfile uv.lock --no-resolve --all-packages --format json` | Exit 0; 24 package entries, including the local project and development packages, no known vulnerabilities returned. Advisory absence is not a code review or a guarantee. |
| High-level `agent-security-scan deps` | Incomplete: offline database absent; online discovery did not recognize dependency inputs, including an exported requirements file. The explicit lockfile scan above supplies the Python advisory evidence. |
| `agent-security-scan --offline ... pins flake.lock` | Incomplete: OSV-Scanner 2.7.0 cannot query Git-commit advisories offline; pins do not inventory installed Nix packages. No source fetch or closure security scan was made. |
| `project-check fast` | Passed: baseline, Ruff format/lint and 542 tests on Python 3.15.0. One existing SQL advisory is a generated list of `?` placeholders with separately bound values (`tracking.py`, `tally`), not an identified SQL injection. |
| `uv build --offline --wheel` and wheel inspection | Passed: `License-Expression: MIT`, `License-File: LICENSE` and the exact MIT notice are in the wheel; no `.env` or `.state/` paths. |
| `project-check full` | Passed: baseline, `nix fmt -- --ci` and `nix flake check`, including VM/host contracts and Ruff pin. No host activation. |
| `bash scripts/vm-smoke`, including `bash scripts/container-test` | Passed in a disposable guest with 3 vCPUs and 6 GiB assigned memory: 542 tests each on Python 3.15.2, 3.13.18 and 3.11.18; the new remote is seeded; native wheels import; no SSH/listener or host-loopback reachability. The 152 MiB container runs as uid 10001, has no egress, solves the offline rehearsal, uses only stand-in providers and submits/finishes both stand-in Arena tasks. The owned VM and disposable disk are cleaned up by the smoke script. |
| `git diff --check`; ignore-rule inspection | Passed; `.env`, `.env.backup` and `.env.production` are ignored, while `.env.example` remains available. |

The first source scan targeted the checkout root and was incomplete because
its filesystem inventory included ignored local paths and hit its limit.
It is discarded as source-coverage evidence; the tracked-source export scan
above replaces it. Reports remain outside Git and matched credential-like
values are not copied into this document.

The gates and VM/container run preceded the final documentation-only update
of this evidence table. No new live-model resistance test, paid benchmark,
competition run, macOS/Windows/ARM execution or full transitive license audit
is included. The repository reported zero issue comments and review comments
on its sole PR and zero releases. Wiki history, edited/deleted comment bodies
and external attachments are not covered by the source and history scanners.
