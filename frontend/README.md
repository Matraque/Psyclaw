# Psyclaw Assistant UI shell

This is Psyclaw's product-facing, text-only local interface. It connects
directly to the existing Python Google ADK Web server. ADK Web remains the
separate developer surface for event traces and debugging; it is not copied or
modified here.

The browser receives only these explicit public connection values:

- `VITE_ADK_URL`
- `VITE_ADK_APP_NAME`
- `VITE_ADK_USER_ID`

Do not put a model credential, provider credential, patient record, or a real
user identifier in a `VITE_*` value. This UI does not choose a model,
transcription provider, or fallback.

## Local development

Install the locked frontend dependencies:

```bash
cd frontend
npm ci
```

In another terminal, start the existing ADK Web server from the repository
root. The agents directory is that repository root, which contains the
`psyclaw/` ADK application package. Keep it bound to localhost and allow only
the Vite development origin:

```bash
uv run psyclaw-adk-web --host 127.0.0.1 --port 8000 --allow-origin http://127.0.0.1:5173
```

Copy `.env.example` to `.env.local`, replace the user ID with a synthetic local
development identity, then launch the frontend. This is not a production
identity scheme: authenticated user/session ownership is a future product and
security boundary.

```bash
cd frontend
npm run dev
```

The three direct-ADK settings must all be present before the composer renders.
They point Assistant UI's `createAdkStream` and `createAdkSessionAdapter` to
the same ADK Web server, so its session API remains the history authority.

`psyclaw-adk-web` starts the official ADK Web server with ADK's SQLite session
service explicitly located at `<PSYCLAW_PATIENT_DIR>/.adk/session.db` (or the
default private patient directory). Assistant UI does not create or manage the
database; ADK remains the session-history authority.

## Credential-free UI check

No model request is needed to check the UI. Create `frontend/.env.local` with:

```bash
VITE_PSYCLAW_DEMO=true
```

The demo uses an in-browser deterministic response only. It is not a therapist,
does not contact an ADK server, and must not be used with real patient data.

## Validation

```bash
npm run lint
npm run test
npm run build
```
