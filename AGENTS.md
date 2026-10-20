# Instructions for AI assistants

Follow https://github.com/romkey/python-ai-rules/blob/main/RULES.md.
Follows python-ai-rules v0.0.0. When re-scanning, apply the entries in
its UPGRADING.md newer than that, then update this line.
With Claude Code, the `python-rules` plugin loads them for you.

Read docs/overview.md first. `main` is protected: work on a branch and
open a pull request; CI and Lint must pass before it can merge.

<!-- Project-specific notes go below. -->

- Protect is reached only through `protect.py` (`ProtectSource`); tests use
  `tests/fakes.py`. Never add real network calls to tests.
- Audit records are append-only: never rewrite or delete them, and keep
  `recorded_at` increasing down the files (clients page by it).
- `contrib/member-zone/` programs run inside Member Zone's image: standard
  library only, and follow Member Zone's docs/audit-logs.md (and, for
  credential programs, docs/credentials.md) protocols.
