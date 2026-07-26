# Psyclaw contributor guide

## Project scope

Psyclaw is a model-agnostic open-source AI psychologist built with Python,
Google ADK, and LiteLLM. Keep the repository, code comments, documentation,
user-facing tool schemas, and agent instructions in English.

This is a public project. Do not overfit product decisions, tools, defaults, prompts, examples, or documentation to the current local user or patient record. Build configurable, general-purpose behavior that works for users in different locations, languages, cultures, and care contexts.

Keep this `AGENTS.md` up to date whenever you discover a durable project convention, safety constraint, architectural decision, validation command, or contributor workflow that future agents need to follow.

## Architecture

- `psyclaw/agent.py` defines `root_agent`, its ADK tool registration, and the
  application-level `app` used to register plugins.
- `psyclaw/instruction.py` contains the authoritative hard-coded system-instruction template and deterministically fills its runtime placeholders with a fresh date, session state, warnings, and patient-record context before every model call.
- `psyclaw/patient_paths.py` is the canonical resolver for the private patient
  workspace; patient tools and transcript storage must use the same resolved root.
- `psyclaw/patient_tools.py` provides the only filesystem capabilities exposed to the agent.
- `psyclaw/transcript.py` owns validated, crash-recoverable transcript storage,
  while `psyclaw/transcript_plugin.py` projects scoped ADK callbacks into it.
- Transcript data stays in the hidden private namespace
  `.transcripts/v1/sessions/<opaque-session-id>/` under the patient workspace.
- Complete full-turn transcript capture is approved fail-closed: a persistence
  failure must stop silent continuation and expose only a sanitized error.
- `psyclaw/default_patient/` contains immutable public defaults with their final filenames.
- `.psyclaw-data/patient/` is the default private runtime workspace. `PSYCLAW_PATIENT_DIR` may override it.
- `tests/` contains deterministic unit tests for implementation behavior and security boundaries.

## Privacy and security

- Never commit real patient records, session notes, API keys, `.env` files, or any other sensitive data.
- Keep patient filesystem access strictly scoped to the configured private workspace (`.psyclaw-data/patient/` by default).
- Do not weaken path traversal, symbolic-link, hidden-file, extension, or file-size protections without explicit approval and accompanying tests.
- Do not introduce general shell, browser, network, or unrestricted filesystem tools into the patient agent without an explicit threat-model review.

## Development workflow

- The tech lead is the sole interface between the product owner and developer
  agents. Developer agents do not receive product-owner directives implicitly;
  every assignment must restate the necessary product decisions, constraints,
  acceptance criteria, allowed files, forbidden scope, tools, validation, and
  handoff format.
- The tech lead must make the required development and validation tools available
  to each agent within the approved scope. Missing tooling, permissions, test
  fixtures, or documentation are orchestration problems for the tech lead to
  resolve, not reasons for an agent to improvise a broader architecture.
- Developer-agent output is never accepted on self-report alone. The tech lead
  inspects every changed file, reruns risk-proportionate tests, validates the
  relevant UI and event traces, checks privacy and scope preservation, requests
  corrections when needed, and only then stages, commits, pushes, or opens a PR.
- The tech lead owns every PR through review and CI resolution. Developer agents
  do not merge, deploy, contact the product owner, or make unapproved product
  decisions.
- Treat the private, Git-ignored `BACKLOG.html` as the local living source of
  truth for product decisions, ticket status, dependencies, and delivery order.
  Update its single inline `backlog` array whenever a ticket changes state or a
  durable decision is made. `BACKLOG.md` is a legacy planning snapshot and must
  not be used as the current status source.
- Every feature begins with a state-of-the-art research ticket and an explicit
  architecture or product decision before implementation.
- Give each implementation or research ticket its own Git worktree and
  `codex/<ticket-id>-<slug>` branch. Avoid parallel tickets that modify the same
  files; declare an integration order when overlap is unavoidable.
- Developer agents receive narrow scope, explicit acceptance criteria, file
  ownership, validation commands, and forbidden changes. They do not merge or
  deploy their own work.
- After deterministic tests and behavioral evaluations, validate every feature
  end to end in ADK Web before opening a pull request.
- The tech lead decides when an integrated slice is stable enough for a
  product-owner real-world test. Before inviting that test, clear known blocking
  defects, complete internal UI validation, and provide a concise runbook with
  prerequisites, expected behavior, privacy cautions, and requested feedback.
  Feed confirmed bugs and product discoveries back into `BACKLOG.html` as small,
  explicit tickets.
- Keep personal report-recipient addresses and other private coordination data
  out of the public repository. Research decisions may be delivered through a
  privately configured channel.
- Keep the README product-facing: use plain English, short sentences, and a
  concise, compelling presentation. Emphasize user benefits such as local
  privacy and confidentiality without explaining internal implementation;
  place technical detail in dedicated contributor or architecture documents.
- State cross-cutting properties such as model choice once where they clarify a
  boundary. Do not repeat labels like "provider-neutral" across titles,
  docstrings, tests, and PR prose when the code already makes the rule clear.
- Use `uv sync` after dependency changes.
- Run `uv run python -m unittest discover -s tests -v` before handing off changes.
- Add deterministic unit tests for tool and path-policy changes. Do not assert non-deterministic LLM prose in unit tests; use an evaluation workflow for agent behavior.
