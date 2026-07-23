# Psyclaw contributor guide

## Project scope

Psyclaw is an open-source AI psychologist built with Python, Google ADK, LiteLLM, and Mistral. Keep the repository, code comments, documentation, user-facing tool schemas, and agent instructions in English.

This is a public project. Do not overfit product decisions, tools, defaults, prompts, examples, or documentation to the current local user or patient record. Build configurable, general-purpose behavior that works for users in different locations, languages, cultures, and care contexts.

Keep this `AGENTS.md` up to date whenever you discover a durable project convention, safety constraint, architectural decision, validation command, or contributor workflow that future agents need to follow.

## Architecture

- `psyclaw/agent.py` defines `root_agent` and its ADK tool registration.
- `psyclaw/instruction.py` contains the authoritative hard-coded system-instruction template and deterministically fills its runtime placeholders with a fresh date, session state, warnings, and patient-record context before every model call.
- `psyclaw/patient_tools.py` provides the only filesystem capabilities exposed to the agent.
- `psyclaw/default_patient/` contains immutable public defaults with their final filenames.
- `.psyclaw-data/patient/` is the default private runtime workspace. `PSYCLAW_PATIENT_DIR` may override it.
- `tests/` contains deterministic unit tests for implementation behavior and security boundaries.

## Privacy and security

- Never commit real patient records, session notes, API keys, `.env` files, or any other sensitive data.
- Keep patient filesystem access strictly scoped to the configured private workspace (`.psyclaw-data/patient/` by default).
- Do not weaken path traversal, symbolic-link, hidden-file, extension, or file-size protections without explicit approval and accompanying tests.
- Do not introduce general shell, browser, network, or unrestricted filesystem tools into the patient agent without an explicit threat-model review.

## Development workflow

- Use `uv sync` after dependency changes.
- Run `uv run python -m unittest discover -s tests -v` before handing off changes.
- Add deterministic unit tests for tool and path-policy changes. Do not assert non-deterministic LLM prose in unit tests; use an evaluation workflow for agent behavior.
