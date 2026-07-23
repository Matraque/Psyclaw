# Psyclaw

OSS AI psychologist with local, persistent memory.

## Development

```bash
uv sync
uv run adk web
```

Set `MISTRAL_API_KEY` in `psyclaw/.env`. Patient records are stored locally in
`.psyclaw-data/patient/` by default and are never committed.

Run tests with:

```bash
uv run python -m unittest discover -s tests -v
```
