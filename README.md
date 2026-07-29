# Psyclaw

OSS AI psychologist with local, persistent memory.

## Start locally

```bash
uv sync
uv run psyclaw-server
```

Set the key for your chosen model in `psyclaw/.env`. Patient records are stored locally in
`.psyclaw-data/patient/` by default and are never committed.

Run tests with:

```bash
uv run python -m unittest discover -s tests -v
```
