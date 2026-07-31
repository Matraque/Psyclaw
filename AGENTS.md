# Psyclaw agent guide

## Product intent

Psyclaw is the open-source AI psychologist. Its product ambition is to deliver
the advantages of an excellent human psychologist without the human drawbacks:
available on demand, with continuity and memory. Do not soften, reinterpret, or
weaken this vision. Do not add new medical promises to the README unless asked.

Psyclaw is inspired by OpenClaw and Hermes and built with Google ADK, LiteLLM,
and Assistant UI. Chat, speech-to-text, and memory must remain model-agnostic;
never impose a provider. Use **user**, never **patient**. Do not add legacy
names, aliases, or migrations while there are no users to migrate.

## Current architecture

Facts on `main`:

- `psyclaw.agent` exposes the ADK `app` and `root_agent`.
- `psyclaw-server` runs the local ADK API server; Assistant UI is the product
  frontend.
- ADK's SQLite session database is the canonical conversation-history store at
  `<user-root>/.adk/session.db` (default:
  `.psyclaw-data/user/.adk/session.db`).
- Markdown memory lives at `<user-root>/memory` (default:
  `.psyclaw-data/user/memory`) and has an official Filesystem MCP toolset
  bounded to that directory. The MCP cannot access the SQLite database.
- The note-taker is still in development. Do not describe or depend on it as a
  merged part of the architecture.

## Privacy and safety

- Never commit, print, fixture, snapshot, or otherwise expose a user directory,
  transcript, audio, key, secret, or `.env` content. Use synthetic test data and
  redact diagnostics.
- Keep agent tools narrowly scoped and explicitly authorized. General shell,
  network, or filesystem access in the agent requires a threat-model review.
- Preserve the private-storage boundary and ADK's canonical session authority.

## Engineering workflow

Research before every feature. Keep architectural research and private product
decisions outside the public repository; the ignored local `BACKLOG.html` is
the working source of truth. The tech lead creates an isolated worktree and
`codex/...` branch for every ticket, using separate worktrees for research and
feature implementation. Build one small PR per feature, preserve user changes,
and avoid conflicts. Remove dead code. Prefer ADK, Assistant UI, and MCP
capabilities before custom infrastructure.

After dependency changes, run `uv sync`. Run the Python suite with
`uv run python -m unittest discover -s tests -v`; in `frontend`, run
`npm test -- --run`, `npm run lint`, and `npm run build`. Perform real UI QA in
the product Assistant UI; use ADK Web only when useful for diagnostics or
traces. Test agent behaviour with evaluations, not deterministic prose-based
LLM tests. Invite the SME only when a slice is stable.

Keep the root README short, simple, and persuasive, with an easy **How to run**;
put technical detail elsewhere.

## Roles and delivery

The tech lead is the sole contact for the product owner. The tech lead gives
each sub-agent explicit acceptance criteria, allowed files, forbidden scope,
and required tests; selects models and reasoning effort proportionate to cost;
parallelizes non-conflicting work; reviews every diff and actual test output;
helps agents unblock; and manages PRs, CI, and review comments. Never rely on a
sub-agent's self-report without inspecting its work.

Sub-agents must not merge, deploy, or contact the product owner. No merge or
deployment happens without appropriate explicit authorization; production is
never implicit.
