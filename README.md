# **Psyclaw**

The open-source mental health AI agent that remembers.

Psyclaw listens, remembers, and is available when you need it. It is inspired
by OpenClaw and Hermes, is model-agnostic and built with Google ADK.

## **Why Psyclaw**

I built Psyclaw after struggling to find a practitioner who accepted new
people. I know that getting support can save a life.

Practitioners may be unavailable. Appointments can be months away. Cost and
fear of judgment can also prevent people from seeking help.

Psyclaw is available on demand. It is open source so we can improve it together
and make support more accessible. Contributions are welcome!

A [2026 Pew Research Center survey](https://www.pewresearch.org/chart/search-and-work-are-the-most-common-uses-for-chatbots-1-in-10-use-these-tools-for-emotional-support/)
found that 10% of U.S. adults had used an AI chatbot for emotional support or
advice.

## **How to run**

You need [uv](https://docs.astral.sh/uv/) and a current
[Node.js LTS](https://nodejs.org/) release.

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

Open `.env` and set `PSYCLAW_MODEL` and `PSYCLAW_API_KEY`.

For example:

```env
PSYCLAW_MODEL=mistral/mistral-medium-latest
PSYCLAW_API_KEY=your-api-key
```

You can use any model supported by LiteLLM. See the
[LiteLLM provider setup](https://docs.litellm.ai/docs/providers).

Speech-to-text is optional, but recommended for a better experience. To use the
microphone, add an STT model and API key to `.env`.

For example:

```env
PSYCLAW_STT_MODEL=mistral/voxtral-mini-latest
PSYCLAW_STT_API_KEY=your-api-key
```

Then, start everything with one command:

```bash
uv run psyclaw
```

On the first run, Psyclaw installs its own dependencies in a local, isolated
environment.

Your browser opens at `http://127.0.0.1:5173`. Press `Ctrl+C` to stop.

## **How it works**

On first use, Psyclaw copies its starter memory files to
`.psyclaw-data/user/`.

This private folder stores local user data and conversation history. Never
commit or share it.

See [how to view local transcripts](docs/viewing-transcripts.md).

Messages and audio are processed by the AI providers you configure.

## **Tests**

```bash
uv run python -m unittest discover -s tests -v
```
