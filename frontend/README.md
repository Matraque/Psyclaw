# Psyclaw Assistant UI shell

This is Psyclaw's product-facing local interface. It connects to Psyclaw's
local server, which exposes the Google ADK API. Development interface can be accessed by running `adk web`.

The browser receives only these explicit public connection values:

- `VITE_ADK_URL`
- `VITE_ADK_APP_NAME`
- `VITE_ADK_USER_ID`
- `VITE_STT_URL` (optional; the local speech-to-text service)

Do not put a model credential, provider credential, user record, or a real
user identifier in a `VITE_*` value. This UI does not choose a model,
transcription provider, or fallback.

## Local development

For normal use, follow the root README. This page is for frontend contributors.

Install the locked frontend dependencies:

```bash
cd frontend
npm ci
```

In another terminal, start Psyclaw's local server from the repository root.
Keep it bound to localhost and allow only the Vite development origin:

```bash
uv run psyclaw-server --host 127.0.0.1 --port 8000 --allow-origin http://127.0.0.1:5173
```

Copy `.env.example` to `.env.local`, replace the user ID with a synthetic local
development identity, then launch the frontend. This is not a production
identity scheme: authenticated user/session ownership is a future product and
security boundary.

```bash
cd frontend
npm run dev
```

## Local speech-to-text

The microphone sends an in-memory recording to a separate loopback service.
The browser never receives its credentials. Add only generic STT settings to
the private root `.env` file:

```bash
PSYCLAW_STT_MODEL=provider/model-name
PSYCLAW_STT_API_KEY=replace-with-a-real-key
# Optional
PSYCLAW_STT_API_BASE=https://your-local-or-provider-endpoint
```

Run the local service in another terminal. It accepts supported browser audio
formats up to 100 MiB. There is no shorter recording timer. The UI runs only
at `http://127.0.0.1:5173`; it stops instead of selecting another port so the
local service can safely recognize it.

```bash
uv run uvicorn psyclaw.transcription_api:app --host 127.0.0.1 --port 8001
```

Set `VITE_STT_URL=http://127.0.0.1:8001` in `frontend/.env.local` to enable
the microphone. A transcription is inserted into the composer for review and
is never sent automatically.

The three direct-ADK settings must all be present before the composer renders.
They point Assistant UI's `createAdkStream` and `createAdkSessionAdapter` to
the same Psyclaw server, so ADK's session API remains the history authority.

`psyclaw-server` starts the official ADK API server with SQLite sessions at
`<PSYCLAW_USER_DIR>/.adk/session.db` (or the default private user
directory). Assistant UI does not create or manage the database; ADK remains
the session-history authority.

## Credential-free UI check

No model request is needed to check the UI. Create `frontend/.env.local` with:

```bash
VITE_PSYCLAW_DEMO=true
```

The demo uses an in-browser deterministic response only. It is not a therapist,
does not contact an ADK server, and must not be used with real user data.

## Validation

```bash
npm run lint
npm run test
npm run build
```
