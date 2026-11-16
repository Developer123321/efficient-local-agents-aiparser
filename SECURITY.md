# Security and supported use

This is experimental hackathon code. There is no supported production release.
The [publication audit](docs/publication-audit.md) documents seven gaps at
revision `99a7254`, reproduced with synthetic workspaces, scripted model
replies and recording tools. A follow-up branch repairs those local code paths
and makes the tests assert the safe behavior; passing them is still not a
safety guarantee, and no live model or platform run measured the repair.

The current agent can act during a read-only inbox task. A lost mutation
reply can leave the same operation callable again. Scanned evidence and
model answers can affect conclusions beyond what their provenance proves.
The optional model loop can disclose a compacted secret and retain executable
URLs in object keys. The submitted solver profile and optional loop have
different exposure; see the audit for the exact tested boundaries.

Experiment with synthetic data, dummy credentials and tools that record
operations without sending them. `HACKLAB_ARENA_ACTIONS=never` withholds the
platform's mutating tools, but does not repair answer integrity or disclosure
through answers. Do not attach real operational authority or sensitive data.

Keep `.env`, `.state/`, traces, datasets and VM disks private. The Dockerfile
uses an allowlist for its build context. Git ignore rules do not remove files
already committed and do not protect a force-added file.

Report issues through this repository's issue tracker using a minimal
synthetic reproduction. Include the revision and profile, expected behavior
and recorded output. Never post keys, personal records, organizer task files
or private model traces. There is no guaranteed response or remediation time.
