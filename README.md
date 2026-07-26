# Psyclaw

Psyclaw is an open-source AI psychologist.

Our ambition is simple: build the best psychologist in the world.

Psyclaw aims to combine the strengths of a great psychologist with support that is always available, consistent, and continuous.

## Always within reach

Psyclaw is designed for conversations at any time, day or night. A good conversation should not disappear when the session ends.

Inspired by the architecture of OpenClaw and Hermes, Psyclaw is an agent that remembers. It preserves continuity across conversations.

Choose the LLM provider and model that suit you. Speech-to-text is coming.

## Confidentiality first

Your patient records stay on your machine by default. You remain in control of your data.

## Current status

Psyclaw is an early project under active development.

## Try it

```bash
uv sync
uv run adk web
```

Patient records are stored locally in `.psyclaw-data/patient/` by default and are never committed.

Run the tests with:

```bash
uv run python -m unittest discover -s tests -v
```
