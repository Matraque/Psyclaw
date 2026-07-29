# Psyclaw

The open-source mental health AI agent that remembers.

Psyclaw listens, remembers, and is available when you need it. It is inspired
by OpenClaw and Hermes, and built with Google ADK.

## Why Psyclaw

I built Psyclaw after struggling to find a practitioner who accepted new
patients. I know that getting support can save a life. Practitioners can be
unavailable, appointments months away, and cost or fear of judgment can create
friction. Psyclaw is available on demand. It is open source so we can improve
this support together.

A [2026 Pew Research Center survey](https://www.pewresearch.org/chart/search-and-work-are-the-most-common-uses-for-chatbots-1-in-10-use-these-tools-for-emotional-support/) found that 10% of U.S. adults had used an AI chatbot for emotional support or advice.

## How to run

You need [uv](https://docs.astral.sh/uv/) and a current Node.js LTS release.

```bash
git clone https://github.com/Matraque/Psyclaw.git
cd Psyclaw
# macOS or Linux
cp .env.example .env
```

On Windows PowerShell, use:

```powershell
Copy-Item .env.example .env
```

Open `.env`. Set `PSYCLAW_MODEL` and, if your chosen provider needs
one, `PSYCLAW_API_KEY`. Models are configurable through LiteLLM. Some providers
need extra environment values; add them to this file. See the [LiteLLM provider
setup](https://docs.litellm.ai/docs/providers).

Then start everything with one command:

```bash
uv run psyclaw
```

Your browser opens at `http://127.0.0.1:5173`. Press `Ctrl+C` to stop.

Speech-to-text is optional. Add its two settings in `.env` when you
want to use the microphone.

History and records are stored locally in `.psyclaw-data/patient/`. Messages,
record context, and audio are sent to the model or speech provider you choose.
Their privacy and retention rules apply. Only local providers keep processing
local. Do not commit this folder or your `.env` file.

## Tests

```bash
uv run python -m unittest discover -s tests -v
```
