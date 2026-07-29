# Psyclaw

The open-source AI psychologist.

Psyclaw listens, remembers, and is available when you need it. Your sessions
and patient record stay on your computer.

## How to run

You need [uv](https://docs.astral.sh/uv/) and Node.js with npm.

```bash
git clone https://github.com/Matraque/Psyclaw.git
cd Psyclaw
# macOS or Linux
cp psyclaw/.env.example psyclaw/.env
```

On Windows PowerShell, use:

```powershell
Copy-Item psyclaw/.env.example psyclaw/.env
```

Open `psyclaw/.env`. Set `PSYCLAW_MODEL` and, if your chosen provider needs
one, `PSYCLAW_API_KEY`. Psyclaw works with any LiteLLM-supported provider.

Then start everything with one command:

```bash
uv run psyclaw
```

Your browser opens at `http://127.0.0.1:5173`. Press `Ctrl+C` to stop.

Speech-to-text is optional. Add its two settings in `psyclaw/.env` when you
want to use the microphone. The recording stays in memory until it is
transcribed or discarded.

Your private data is stored locally in `.psyclaw-data/patient/`. Do not commit
this folder or your `.env` file.

## Tests

```bash
uv run python -m unittest discover -s tests -v
```
