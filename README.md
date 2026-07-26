# Psyclaw

Psyclaw is an open-source AI psychologist. Our ambition is simple: build the best psychologist in the world.

Psyclaw aims to combine the strengths of a great psychologist with support that is always available, consistent, and continuous.

## Always within reach

Psyclaw is designed for conversations at any time, day or night. A good conversation should not disappear when the session ends.

The current local version uses Mistral. Provider choice is being generalized.

## Confidentiality first

Your patient records stay on your machine by default. You remain in control of your data.

## Current status

Psyclaw is an early project under active development.

## Try it

```bash
uv sync
uv run adk web
```

Set `MISTRAL_API_KEY` in `psyclaw/.env` before starting. Patient records are stored locally in `.psyclaw-data/patient/` by default and are never committed.

Run the tests with:

```bash
uv run python -m unittest discover -s tests -v
```
