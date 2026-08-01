# View local transcripts

Psyclaw stores conversation history locally in a SQLite database. It is not a
file to share: conversations can be sensitive.

The default database is:

```text
.psyclaw-data/user/.adk/session.db
```

If you set `PSYCLAW_USER_DIR`, use:

```text
<PSYCLAW_USER_DIR>/.adk/session.db
```

## In VS Code

Install the [SQLite](https://marketplace.visualstudio.com/items?itemName=alexcvzz.vscode-sqlite)
extension by alexcvzz. Open `session.db`, then browse its tables and run only
`SELECT` queries. Do not edit the database while Psyclaw is running.

## In a terminal

You can also open it read-only:

```bash
sqlite3 -readonly .psyclaw-data/user/.adk/session.db
```

Keep this database local. Never commit it, share it, upload it, or open it with
a cloud service.
