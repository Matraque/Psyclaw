# MEM-R02 — Hermes Agent memory-management research

Date: 2026-07-27

Scope: research only; no implementation or dependency changes

Primary Hermes snapshot: [`5646fed`](https://github.com/NousResearch/hermes-agent/commit/5646fed97eac67c5ec5b21e5c491309d8c97639d)

## Executive summary

Hermes provides a useful precedent, but not a component Psyclaw should import.
Its strongest idea is a strict separation between three different things:

1. the complete conversation record in canonical SQLite tables;
2. a rebuildable FTS5 projection used to retrieve literal transcript excerpts;
3. small, curated Markdown memory files that are injected into future sessions.

Psyclaw has already made the equivalent first architectural decision: Google
ADK 2.5 SQLite is the only canonical transcript store. That decision should not
be reopened. Psyclaw should also keep the note-taker's Markdown files outside
the transcript database and expose only that memory directory to the official
MCP Filesystem server. There is no reason to add FTS, embeddings, RAG, another
database, or a Hermes-style review daemon now.

The Hermes review loop is less universal than its documentation can suggest.
Current code does **not** review memory after every turn. By default, it schedules
a memory review every ten user turns, and only after a completed, uninterrupted
response. The review is a detached, best-effort thread. It replays the full
conversation to the main model by default, or a bounded digest to a separately
configured model, and may add, replace, or remove entries. Approval is optional
and off by default. These are product choices optimized for a general coding
agent, not proven defaults for sensitive psychological records.

What Psyclaw should reuse later is narrower: bounded curated memory; explicit
add/update/correction/no-op operations; isolation of the reviewer from the
canonical transcript; an optional separately configured review model; source
links from memories back to canonical events; and keyword snippets with nearby
context before any semantic retrieval. The precise cadence, autonomous writes,
prompts, skill-learning machinery, large Hermes schema, and cross-profile recall
are Hermes-specific and should not be copied.

## Research method and evidence boundary

This report inspected the official NousResearch repository at the pinned commit
above. Important claims link to that immutable source rather than to `main`.
Hermes is changing rapidly, so this report describes that snapshot, not a stable
public API. Documentation was checked against implementation; where they differ,
the implementation controls the finding.

The comparison is deliberately narrow:

- OpenClaw is pinned to
  [`52e5192`](https://github.com/openclaw/openclaw/commit/52e519220cea9cf91904e927d63d2dc6ff8f8b78).
- Google ADK is pinned to the official Python v2.5.0 commit
  [`1e93d82`](https://github.com/google/adk-python/commit/1e93d82fdb5470bcb6d09caa1f3ade13058b921e).
- The official MCP Filesystem server is pinned to
  [`d31124c`](https://github.com/modelcontextprotocol/servers/commit/d31124c982401739917fd817c2a59db344529c16).

“Proven” below means directly implemented, documented, and supported by tests or
recovery paths in the inspected project. It does not mean clinically validated.

## 1. Hermes data model

### 1.1 Canonical sessions and transcripts

Hermes documents `~/.hermes/state.db` as the single session store for CLI and
gateway conversations. Legacy session JSONL files are no longer read or written;
the small `sessions.json` file is only a gateway routing index, not a transcript
store ([official session documentation](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/website/docs/user-guide/sessions.md#L9-L27),
[storage clarification](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/website/docs/user-guide/sessions.md#L637-L687)).

The schema is deliberately richer than a chat transcript:

- `sessions` stores source, user/routing identifiers, model configuration,
  system-prompt snapshot, lineage, lifecycle timestamps, usage/cost counters,
  workspace metadata, archive/pin state, and compression state.
- `messages` uses an autoincrement integer ID and stores session ID, role,
  content, tool call/result fields, timestamp, token and finish metadata,
  provider reasoning fields, platform message identity, active/compacted flags,
  and display/API sidecars.

The exact current DDL is in
[`SCHEMA_SQL`](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L1143-L1275).
Insertion order is the message ID, not the timestamp; the read path explicitly
uses `ORDER BY id` and supports offset/limit pagination
([source](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L6815-L6868)).

Hermes persists more than visible user and assistant prose. Its flush path writes
user, assistant, and tool messages, tool calls and results, finish metadata, and
optional model reasoning fields. It deliberately excludes private retry/nudge
scaffolding and reduces multimodal tool results to text summaries rather than
copying base64 media into SQLite
([flush policy](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/run_agent.py#L1880-L2119)).
Before executing side-effecting tools, Hermes persists the assistant tool-call
turn; if that append fails, tool execution is stopped
([source](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/agent/conversation_loop.py#L5790-L5840)).

This is “complete” in the framework-event sense, not “every byte ever observed.”
Synthetic recovery prompts are intentionally absent, and media is represented
by derived text or paths. That distinction matters for Psyclaw: a canonical
clinical conversation record should be complete for normalized ADK events, not
an uncontrolled archive of raw microphone files or hidden provider payloads.

### 1.2 SQLite, WAL, and FTS5 structure

The canonical `sessions` and `messages` tables are ordinary SQLite tables. Search
is a derived external-content FTS5 index whose row IDs reference `messages.id`.
Insert, delete, and content-update triggers synchronize `content`, `tool_name`,
and `tool_calls`
([FTS DDL](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L1326-L1382)).

Current Hermes also carries optional specialized search projections:

- a trigram FTS5 index for substring/CJK recovery, excluding tool rows to avoid
  indexing large machine output;
- an optional loadable CJK-bigram tokenizer/index;
- rebuild progress markers because large index conversions can be deferred.

These are performance refinements, not canonical data
([trigram design](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L1384-L1451),
[CJK design](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L1453-L1538)).

WAL is preferred, but Hermes falls back to rollback-journal `DELETE` mode when
WAL locking is unsafe or unsupported. Writes use a process lock,
`BEGIN IMMEDIATE`, bounded randomized retries for `busy`/`locked`, rollback on
failure, and periodic passive checkpoints
([journal policy](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L587-L742),
[write transaction](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L2382-L2450)).

The important reusable property is not “use Hermes's tables.” It is: canonical
rows remain authoritative and search indexes can be discarded and rebuilt.
Hermes's repair path backs up the database, checks both FTS reads and a rolled-
back FTS-triggered write, then progressively rebuilds FTS, reindexes B-trees, or
drops only FTS schema. It explicitly preserves `sessions` and `messages`
([health probe](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L846-L967),
[repair policy](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L975-L1139)).

### 1.3 Short memory, long memory, and raw history

Hermes has three distinct recall layers:

| Layer | Storage | Model access | Purpose |
|---|---|---|---|
| Active/short context | current session messages, possibly compacted | automatically in the current request | immediate conversational continuity |
| Curated persistent memory | `MEMORY.md` and `USER.md` | bounded frozen snapshot at session start; live tool responses show changes | durable high-signal facts and preferences |
| Full historical record | canonical SQLite messages + derived FTS | explicit `session_search` calls | literal past-conversation evidence |

The built-in files live in the active Hermes profile's `memories/` directory.
Defaults are 2,200 characters for `MEMORY.md` and 1,375 for `USER.md`; entries
are separated by `§`. Writes change disk immediately, but the system-prompt
snapshot does not change until a new session, preserving prompt-cache stability
([implementation](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/memory_tool.py#L1-L24),
[load and snapshot](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/memory_tool.py#L198-L261)).

The memory tool supports add, replace, and remove. Replace/remove identify an
entry by a unique substring. A batch can atomically combine removals,
replacements, and additions against the final character budget
([tool contract](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/memory_tool.py#L1156-L1246)).
This simple correction operation is more relevant to Psyclaw than Hermes's exact
file format.

Raw transcript, FTS index, and curated memory therefore have different
lifecycles. A memory entry does not replace or edit its source transcript. An
FTS rebuild does not alter memory files. A session compaction may hide old rows
from live context while marking them as compacted and keeping them searchable
([source](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L6763-L6813)).

### 1.4 Optional external memory providers

Hermes also ships integrations for eight external memory providers. At most one
can be active, and it is additive to the built-in Markdown memory. Depending on
the provider, Hermes can inject provider context, prefetch before turns, sync
completed turns, extract on session end, mirror built-in writes, and expose
provider-specific tools
([official provider architecture](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/website/docs/user-guide/features/memory-providers.md#L8-L39)).
These plugins may send conversation material to a cloud service and introduce
provider-specific identity, cadence, cost, and retention semantics. They are not
part of the robust local core assessed here and are not candidates for Psyclaw's
lean memory slice. The background review explicitly disables them to avoid
ingesting its synthetic review harness into the user's provider namespace.

## 2. Hermes transcript search, precisely

### 2.1 Query and ranking

`session_search` makes no LLM call. Its modes are inferred from arguments:
discovery by query, scroll by session/message anchor, read by session ID, and
browse with no arguments
([module contract](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/session_search_tool.py#L1-L26),
[dispatch](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/session_search_tool.py#L839-L954)).

Discovery defaults to user and assistant roles. It accepts FTS5 terms, quoted
phrases, boolean operators, and prefix wildcards. Default ordering is FTS5 BM25
rank. `newest` and `oldest` make message timestamp primary and rank the
tiebreaker
([query implementation](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L7927-L8118)).

The tool scans up to 300 matching rows, demotes recurring automation sessions
below interactive sessions, excludes tool/subagent sessions by default, removes
the current live lineage, deduplicates by session lineage, and returns one best
hit per lineage. The public result limit is clamped to 1–10, default 3
([ranking policy](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/session_search_tool.py#L28-L49),
[discovery](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/session_search_tool.py#L616-L788)).

For CJK queries, Hermes may use its specialized CJK/bigram or trigram index; it
falls back to bounded-result `LIKE` scans when a query cannot be represented by
the available tokenizer. That complexity is a useful warning: FTS quality is
language-sensitive, and a future Psyclaw keyword prototype must evaluate the
actual supported languages rather than assume English tokenization generalizes.

### 2.2 Snippets and context

SQLite's `snippet()` returns a highlighted excerpt with a 40-token budget.
Discovery then adds:

- a ±5-message window around the matching message;
- the first three non-empty user/assistant messages as a start bookend;
- the last three as an end bookend;
- the anchor message ID and counts indicating whether more messages exist
  before and after.

The anchored view is implemented over canonical rows, not the FTS shadow table
([window primitive](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L6891-L6981),
[bookends](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L6983-L7135)).

Scroll accepts a session ID, anchor message ID, and a window clamped to 1–20.
The first or last returned ID becomes the next backward or forward anchor; the
boundary message intentionally overlaps as an orientation marker
([tool schema](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/session_search_tool.py#L975-L1079)).

There is no discovery cursor exposed to the model. Although the lower-level
database search accepts `offset`, the tool always scans from offset zero and
offers only a larger top-N limit. Scrolling paginates *within a discovered
session*, not across additional search hits. A full-session read is also
bounded for large sessions (first 20 and last 10), rather than being an
unrestricted transcript dump. Psyclaw should preserve this distinction between
search-result pagination and contextual scrolling.

## 3. Hermes background memory review

### 3.1 Trigger and timing

The module docstring says review may run after every turn, and the user guide
uses similar shorthand. The executable trigger is more specific:

- memory review default cadence is `memory.nudge_interval = 10` user turns;
- it only arms if the built-in memory store and memory tool are enabled;
- the counter is reconstructed from resumed history;
- review starts only after a final response and only when the turn was not
  interrupted;
- a separate skill cadence counts tool-loop iterations, also defaulting to 10.

See the initialization defaults
([source](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/agent/agent_init.py#L1601-L1634)),
turn counter
([source](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/agent/turn_context.py#L525-L572)),
and final trigger
([source](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/agent/turn_finalizer.py#L631-L661)).

The trigger starts a daemon thread after the response is delivered. The main
turn does not wait for review completion. This minimizes response latency, but
it also makes review best-effort: process exit, a failed auxiliary request, or a
write race can lose that review without invalidating the completed conversation.

### 3.2 Model and context received

The main chat model is the default reviewer. Hermes reuses the live provider,
model, credentials, cached system prompt, and complete conversation snapshot to
benefit from a warm prompt cache. A user may configure
`auxiliary.background_review.{provider,model}`. If it resolves to a different
model, Hermes sends a digest: the most recent 24 messages verbatim and older
user/assistant text reduced to short role-labelled lines
([runtime selection and digest](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/agent/background_review.py#L29-L149)).

The memory-only prompt asks whether the user revealed durable persona,
preferences, personal details, or behavioral expectations. The reviewer can
write with the memory tool or return “Nothing to save.”
([prompt](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/agent/background_review.py#L154-L169)).
This is a deliberately broad coding-assistant policy. It is not an adequate
clinical memory taxonomy, evidence rule, or consent policy.

### 3.3 Writes, corrections, and no-op

Successful review operations are ordinary memory-tool calls:

- `add` creates a compact entry;
- `replace` corrects one uniquely matched entry;
- `remove` deletes one;
- `operations` applies several changes atomically against the final size cap;
- no tool call plus “Nothing to save” is the expected no-op.

The reviewer has a runtime whitelist limited to memory and skill management.
Dangerous-command approval auto-denies in the worker thread. External memory
providers are disabled so the review harness is not synchronized as if it were
a real user conversation
([review fork](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/agent/background_review.py#L623-L760),
[tool restriction](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/agent/background_review.py#L813-L879)).

Most importantly, the fork shares the parent session ID for cache reuse but sets
`_persist_disabled = True`, disables session JSON, disables compression, and
does not finalize the parent. Thus its review instruction, tool calls, and
answer cannot pollute the canonical conversation
([isolation](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/agent/background_review.py#L761-L812)).
Hermes also strips legacy review-harness messages that older versions had
accidentally persisted, demonstrating that this is a learned production
invariant rather than theoretical neatness
([cleanup](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L388-L441)).

### 3.4 Permission and approval behavior

Memory writes are autonomous by default. `memory.write_approval: true` changes
the behavior:

- foreground interactive CLI writes can be approved or denied inline;
- gateway, script, and background-review writes are staged under
  `<HERMES_HOME>/pending/memory/`;
- staged records survive restart and can be approved or rejected later;
- staging failure is safe with respect to memory—it logs and loses the pending
  proposal rather than committing an unapproved write.

The gate and decision matrix are explicit in
[`write_approval.py`](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/write_approval.py#L1-L44)
and
[`evaluate_gate`](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/write_approval.py#L231-L316).
This is useful prior art for Psyclaw, but default-off approval is not evidence
that autonomous clinical-note writes are acceptable.

### 3.5 Failure and recovery semantics

Hermes treats its two persistence classes differently:

- Canonical SQLite writes are transactional and retried under contention. A
  tool-call event must persist before the tool is run. Corrupt derived FTS is
  rebuilt once from canonical messages, and an operator repair command can
  back up and repair the store.
- The general message flush catches append errors and returns `False`; some
  lifecycle paths continue with the session database unavailable. Hermes is
  therefore not uniformly fail-closed for all conversation writes. Its own
  documentation tells users to watch for a “session store unavailable” warning.
- Background review is explicitly best-effort. Spawn exceptions are ignored by
  the finalizer; worker errors become warnings/auxiliary-failure notices, and
  there is no durable review job queue or exactly-once replay
  ([worker error path](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/agent/background_review.py#L918-L969)).
- Memory-file writes use a lock, a same-directory temporary file, `fsync`, and
  atomic replace. They refuse mutation when a file is unreadable or external
  edits would not round-trip, preserving a backup instead of silently
  overwriting drift
  ([file safety](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/memory_tool.py#L56-L115),
  [atomic write](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/memory_tool.py#L863-L896)).

Psyclaw should reuse the recovery separation—canonical events must remain valid
even if a derived index or memory review fails—but retain its already approved
stronger fail-closed policy for canonical ADK event persistence.

## 4. Isolation, privacy, and audio

### 4.1 User and session isolation

Hermes session IDs and platform routing keys separate active contexts. Gateway
groups are per-user by default when a platform supplies a participant ID;
threads may be shared, and missing participant identity can fall back to a
shared room session
([official behavior](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/website/docs/user-guide/sessions.md#L594-L632)).
Profiles use separate Hermes homes and therefore separate memory files and
databases.

However, the built-in discovery query is profile-wide, not user-scoped. It
filters sources and roles, but does not constrain `sessions.user_id`; it even
supports explicit cross-profile reads
([search query](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/hermes_state.py#L8055-L8120),
[cross-profile dispatch](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/tools/session_search_tool.py#L875-L915)).
That is reasonable for a single owner's personal agent but is not a tenant
authorization boundary. Psyclaw must scope every future retrieval request by
its ADK `app_name`, `user_id`, and `session_id` ownership contract and must not
copy Hermes's profile-wide search assumption.

Curated built-in memory is also profile-scoped rather than session-scoped. That
is precisely why Psyclaw's memory directory must be patient-scoped and never a
global assistant profile.

### 4.2 Audio data

Hermes gateway voice messages are transcribed before the agent turn when STT is
configured. The enriched text becomes the user content; a failed transcription
becomes a neutral text marker. Audio file attachments can instead produce a
path-bearing context note. The official session documentation is explicit that
paths and derived text may be persisted, but raw image/audio/binary bytes are
not repeatedly copied into future prompts
([docs](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/website/docs/user-guide/sessions.md#L29-L48),
[STT enrichment](https://github.com/NousResearch/hermes-agent/blob/5646fed97eac67c5ec5b21e5c491309d8c97639d/gateway/run.py#L17856-L17946)).

This does not mean Hermes never stores audio on disk: gateway media is cached
and path references may enter messages. Psyclaw's already approved rule is
stricter and simpler: the canonical conversation receives only the editable STT
text sent by the user; no raw microphone audio or cache path belongs in the ADK
event store or note-taker memory.

## 5. What is robust versus Hermes-specific

### Robust, reusable ideas

- **One canonical transcript, rebuildable indexes.** Search corruption must not
  modify or redefine the source conversation.
- **Transcript and curated memory are different products.** Literal history is
  evidence; curated memory is a small, corrigible interpretation.
- **Bounded memory beats unbounded prompt injection.** A hard budget forces
  consolidation and predictable context cost.
- **Explicit correction operations.** Add/update/remove/no-op are testable and
  easier to audit than free-form whole-file rewrites.
- **Reviewer transcript isolation.** Review prompts and reviewer outputs must
  never appear as user conversation events.
- **Keyword hit plus nearby canonical context.** A snippet, source event ID,
  bounded before/after window, and scroll are a strong first retrieval UX.
- **Optional write approval and visible changes.** Sensitive autonomous writes
  need a reviewable proposal or at least an inspectable audit trail.
- **Language-specific retrieval evaluation.** FTS tokenizers are not universally
  equivalent across English, French, CJK, and mixed-script text.

### Hermes-specific choices not to copy

- its large session schema, custom WAL/FTS migrations, repair machinery, and
  three search-index variants;
- profile-wide search and optional cross-profile access;
- a coding-agent split between `MEMORY.md` and `USER.md` with Hermes's exact
  character limits and `§` syntax;
- a fixed ten-turn cadence and daemon-thread execution;
- full-conversation replay for memory review;
- one reviewer that also edits procedural skills;
- default autonomous writes and broad “personal details worth remembering”
  prompt;
- reasoning/tool payload storage beyond what ADK already owns;
- raw attachment caches and path-bearing transcript notes.

The Hermes code has accumulated many defenses for concurrency, prompt-cache
parity, stale index rebuilds, legacy migrations, compaction lineages, and
multiple gateway platforms. Reimplementing that surface in Psyclaw would be the
opposite of lean reuse.

## 6. Targeted comparison

### 6.1 Google ADK 2.5 SQLite: keep it canonical

ADK 2.5 `SqliteSessionService` already gives Psyclaw the required transcript
boundary:

- sessions are keyed by app, user, and session;
- each event stores the serialized ADK `Event` JSON;
- the primary event key includes app, user, session, and event identity;
- `get_session` returns events ordered by timestamp inside that scope;
  ordering between events with equal timestamps is unspecified;
- event append and state/session updates commit together.

See the official v2.5.0
[`SqliteSessionService` schema](https://github.com/google/adk-python/blob/1e93d82fdb5470bcb6d09caa1f3ade13058b921e/src/google/adk/sessions/sqlite_session_service.py#L43-L93),
[`get_session`](https://github.com/google/adk-python/blob/1e93d82fdb5470bcb6d09caa1f3ade13058b921e/src/google/adk/sessions/sqlite_session_service.py#L231-L295),
and
[`append_event`](https://github.com/google/adk-python/blob/1e93d82fdb5470bcb6d09caa1f3ade13058b921e/src/google/adk/sessions/sqlite_session_service.py#L371-L467).
The Runner appends the user event before model execution and each non-partial
output event before yielding it
([user event](https://github.com/google/adk-python/blob/1e93d82fdb5470bcb6d09caa1f3ade13058b921e/src/google/adk/runners.py#L798-L832),
[output events](https://github.com/google/adk-python/blob/1e93d82fdb5470bcb6d09caa1f3ade13058b921e/src/google/adk/runners.py#L1481-L1518)).

ADK does not provide Hermes's FTS snippets, review loop, or curated files. That
is an advantage for the current slice: Psyclaw needs none of them yet. Adding
tables or triggers to the ADK-owned database would couple Psyclaw to upstream
migrations. A future index must consume the public session API and live in a
separate, disposable projection.

One limitation should remain explicit: ADK stores complete framework events,
not a ready-made two-role clinical transcript. Views and future indexers must
select normalized user text and visible assistant text while retaining event IDs
for provenance. They must not create a second write path or infer an insertion
order for equal-timestamp events. Incremental watermarks and idempotency must be
defined from a deterministic set of event IDs, never from assumed list order.

### 6.2 Official MCP Filesystem: a write surface, not a memory engine

The official Filesystem server can read, write/overwrite, edit, list, move,
search, and inspect files inside allowed directories. Command-line directories
or MCP roots define its scope, and tool annotations distinguish read-only and
destructive operations
([official README](https://github.com/modelcontextprotocol/servers/blob/d31124c982401739917fd817c2a59db344529c16/src/filesystem/README.md#L11-L71),
[tools and annotations](https://github.com/modelcontextprotocol/servers/blob/d31124c982401739917fd817c2a59db344529c16/src/filesystem/README.md#L73-L193)).

It does not provide memory taxonomy, provenance, conflict resolution,
idempotency semantics for note content, write approval, or an audit ledger.
Those responsibilities remain in the note-taker's instruction/tool wrapper and
deterministic tests. MCP roots are a declared boundary, not sufficient OS
sandboxing by themselves; the official protocol guidance says actual security
must also use filesystem permissions or sandboxing
([MCP client guidance](https://modelcontextprotocol.io/docs/learn/client-concepts#roots)).

Psyclaw should therefore launch the pinned official server over stdio with only
the patient memory directory. Root bounding alone is not operation-level least
privilege: the official server also exposes destructive tools such as
`move_file`. Psyclaw may expose only the selected minimum tools if the ADK client
integration proves that its tool filter is enforced, as required by FILES-D02.
If that enforcement cannot be demonstrated, the integration must be treated as
unavailable and must not claim least privilege by operation. In every case, the
server root must exclude the ADK SQLite database, transcript directory, media
cache, and entire patient root. `write_file` overwrites and `edit_file` is
non-idempotent; the note-taker should prefer read-before-edit plus narrow edits,
with tests for corrections and retry behavior.

### 6.3 OpenClaw: convergent separation, more machinery

Current OpenClaw independently converges on the same boundary. Its active
runtime uses per-agent SQLite session/transcript state and treats legacy JSONL
as migration/archive material
([session architecture](https://github.com/openclaw/openclaw/blob/52e519220cea9cf91904e927d63d2dc6ff8f8b78/docs/reference/session-management-compaction.md#L14-L42)).
Its durable `MEMORY.md` and `memory/*.md` files remain distinct from transcripts
([memory model](https://github.com/openclaw/openclaw/blob/52e519220cea9cf91904e927d63d2dc6ff8f8b78/docs/concepts/memory.md#L10-L54)).
The built-in memory engine chunks those Markdown files into a separate SQLite
keyword/vector/hybrid index
([engine](https://github.com/openclaw/openclaw/blob/52e519220cea9cf91904e927d63d2dc6ff8f8b78/docs/concepts/memory-builtin.md#L8-L20),
[indexing](https://github.com/openclaw/openclaw/blob/52e519220cea9cf91904e927d63d2dc6ff8f8b78/docs/concepts/memory-builtin.md#L80-L91)).
It can run a pre-compaction memory flush and optional later dreaming/promotion
([source](https://github.com/openclaw/openclaw/blob/52e519220cea9cf91904e927d63d2dc6ff8f8b78/docs/concepts/memory.md#L212-L289)).

This supports the architecture, not the timing. OpenClaw's hybrid index,
pre-compaction flush, dreaming lanes, and active-memory transcript recall are
later-stage systems. They do not justify adding RAG to Psyclaw before the
note-taker contract is implemented and evaluated.

## 7. Recommended lean Psyclaw architecture

```text
Assistant UI / ADK Web
          |
          v
Google ADK Runner
          |
          v
ADK 2.5 SqliteSessionService
  <patient-dir>/.adk/session.db
  CANONICAL: complete ADK events
          |
          +-- bounded public-API read --> note-taker invocation
          |                                  |
          |                                  v
          |                         official MCP Filesystem
          |                         memory-directory root only
          |                                  |
          |                                  v
          |                         curated Markdown memory
          |
          +-- later --> disposable keyword index --> sourced snippets
```

### Stage 0 — preserve the transcript foundation (already chosen)

- Keep ADK SQLite as the sole canonical transcript.
- Keep explicit patient-private placement and fail-closed event persistence.
- Store STT output only as the user-edited text that is actually sent.
- Do not add callbacks, duplicate transcript writers, FTS triggers, embeddings,
  or memory tables.

### Stage 1 — implement and evaluate the note-taker, without retrieval

- Invoke the already approved synchronous single-turn note-taker through the
  agent-as-tool boundary.
- Give it a bounded set of new events identified by stable ADK event IDs, not
  an unbounded lifetime transcript, an assumed insertion order, or direct
  SQLite access. Its watermark must be a deterministic set of processed event
  IDs so equal timestamps cannot skip or duplicate work.
- Give only the official MCP Filesystem server rooted at the curated-memory
  directory. The ADK client integration must prove enforcement of the exact
  read/list/write/edit tool filter required by FILES-D02. If it cannot, keep the
  integration unavailable rather than claiming operation-level least privilege.
- Require a structured outcome: `no_op`, `add`, `update`, or `correct`, plus the
  affected logical memory target and source event IDs.
- Keep the psychologist unable to write memory. It may later receive narrow
  read-only memory tools.
- Evaluate precision, correction handling, idempotent repeat invocation,
  latency/cost, sensitive-data minimization, and persistence failure behavior
  before changing invocation cadence or adding callbacks.

Hermes contributes operation semantics and isolation invariants here, not its
review thread or prompt.

### Stage 2 — harden provenance and recovery after the first evaluation

- Store source event IDs in or beside each curated note so a human can trace an
  interpretation back to canonical evidence.
- Add a deterministic change receipt or append-only audit record outside the
  prompt-injected memory content. The receipt records proposed/applied/no-op,
  affected file, before/after digest, source events, model configuration role,
  and error—but never raw patient prose in logs.
- Define retry identity from a deterministic set of source event IDs so a crash
  cannot apply the same consolidation twice. Never derive it from retrieval or
  insertion order, including when timestamps are equal.
- Add an approval/proposal mode only if the product decision or evaluation risk
  warrants it. Do not silently import Hermes's default-off policy.

### Stage 3 — keyword transcript retrieval, only when RAG work starts

- Build a separate disposable index from ADK's public session API.
- Start with FTS keyword search, not embeddings.
- Index normalized visible text with `(app_name, user_id, session_id, event_id,
  timestamp, role)` provenance.
- Return a small highlighted snippet, ±N canonical neighboring events, and a
  cursor/anchor for scrolling. Never inject entire sessions by default.
- Scope every query before ranking; authorization is not a post-filter.
- Support full rebuild and report incomplete-index state.
- Evaluate French, English, accents, Unicode, corrections, negation, and the
  supported multilingual population before selecting tokenizers.

Hermes's 40-token snippet, ±5 window, bookends, scroll anchors, BM25 default,
and index-rebuild signaling are good prototype hypotheses, not requirements.

### Stage 4 — consider semantic/hybrid retrieval and consolidation scheduling

Only after keyword retrieval and the note-taker are separately evaluated:

- compare keyword, semantic, and hybrid retrieval on a privacy-safe evaluation
  corpus;
- decide whether a cheaper auxiliary model is accurate enough for review;
- decide whether review is synchronous per completed turn, periodic by event
  watermark, pre-compaction, explicit, or queued background work;
- add a durable job/watermark if background review becomes asynchronous;
- consider longer-horizon consolidation only with provenance, corrections,
  deletion propagation, and observable no-op behavior.

This is the earliest point where OpenClaw dreaming or Hermes auxiliary review
should influence implementation.

## 8. Advantages, disadvantages, and risks

| Choice | Advantages | Disadvantages / risks |
|---|---|---|
| ADK SQLite remains canonical | no duplicate writes; native resume; app/user/session scope; fewer migrations | framework-event schema needs a read projection; ADK DB is unencrypted at rest |
| Curated Markdown via official MCP Filesystem | inspectable, editable, portable; reuses maintained server | roots still expose destructive tools such as `move_file`; operation-level least privilege exists only if the ADK client tool filter is proven |
| No RAG now | smallest privacy and correctness surface; avoids premature tokenizer/embedding decisions | no automatic recall of old transcripts yet |
| Synchronous note-taker first | observable trace and error per turn; simpler testing | adds latency/cost; must avoid blocking a valid canonical transcript on optional memory failure |
| Later derived FTS | literal, explainable hits; rebuildable; no embedding disclosure | keyword mismatch; multilingual tokenization; index contains sensitive duplicate text |
| Optional auxiliary reviewer later | independent cost/model configuration | sends sensitive context to another configured model; digest can omit corrections; another failure mode |

Key privacy/security risks and mitigations:

- **Memory poisoning or false inference:** require source IDs, correction tests,
  narrow write policy, visible changes, and potentially staged approval.
- **Prompt injection persisted as memory:** distinguish user-authored facts from
  tool/external content; never treat retrieved text as instructions; evaluate
  deterministic content scanning without relying on it as the sole defense.
- **Cross-patient recall:** patient-specific roots and ADK user scope must be
  applied before any read/search. Never use a profile-wide FTS query.
- **MCP filesystem escape or overbreadth:** root only the memory directory,
  pin the package, use stdio, retain OS permissions/sandboxing, and test
  traversal/symlink behavior at integration boundaries. Treat the integration
  as unavailable unless the ADK client tool filter is proven to exclude
  unapproved destructive operations.
- **Sensitive duplication:** a future FTS/vector store is another copy of
  patient text. Document deletion/rebuild behavior and keep it local,
  Git-ignored, and outside agent filesystem access.
- **Model-provider disclosure:** each conversational, review, embedding, or STT
  role must be explicitly user-configured. Do not silently route review to an
  auxiliary cloud model.
- **Raw audio retention:** pass only final editable transcript text to ADK and
  memory; delete temporary audio according to the voice subsystem policy.
- **Unbounded review context:** send only the events named by a deterministic
  set of new canonical event IDs, plus the bounded curated-memory state needed
  to reconcile them.
- **Silent background loss:** if background work is later introduced, use a
  durable watermark/job status; a daemon thread is not a recovery protocol.

SQLite-at-rest encryption remains outside this ticket. Local placement and
permissions reduce exposure but do not protect against a compromised account or
device. That should be tracked as a separate threat-model decision rather than
smuggled into the memory feature.

## 9. Decisions already made versus real open decisions

### Already decided; do not reopen in MEM-R02 follow-up

- ADK 2.5 SQLite under the private patient directory is the only canonical
  conversation store.
- Transcript persistence is fail-closed; no completed event is silently skipped.
- The first transcript slice has no RAG, retrieval, prompt injection, or memory
  consolidation.
- Raw microphone audio is not part of the transcript; STT contributes text.
- The psychologist does not own patient-file writes.
- The memory specialist is a synchronous, configurable, single-turn agent-as-a-
  tool, and its result remains in ADK events rather than fabricating a user turn.
- The future note-taker uses the official MCP Filesystem server rooted only at
  the modifiable memory directory; transcripts remain outside that root.
- The psychologist gets narrow read-only memory access later, while historical
  transcript access belongs to a retrieval tool.
- Provider/model roles are explicitly configured, with no project default or
  fallback order.

### Open decisions that actually require an explicit answer

1. **Curated-memory taxonomy and file contract:** which statements belong in
   profile, care plan, preferences, risk/safety facts, and session notes; which
   are forbidden; what source/provenance syntax is stable and human-editable.
2. **Write-consent policy:** direct application with visible audit/revert,
   staged approval for every write, or staged approval only for defined
   high-impact categories. Hermes proves the mechanism is feasible but does not
   settle the clinical policy.
3. **Correction/conflict precedence:** how explicit user correction, later
   contradictory statements, clinician-authored material, and inferred facts
   interact. “Replace by substring” is an operation, not a truth policy.
4. **Memory failure semantics:** canonical conversation persistence must remain
   fail-closed, but should a note-taker/MCP failure fail the user-visible turn,
   return a visible partial-success warning, or create a durable retry job?
   Synchronous MVP evaluation should measure this before background retries are
   designed.
5. **Retention/deletion propagation:** when a user deletes or corrects canonical
   source events, how are derived notes, future indexes, and audit receipts
   invalidated or marked?

Cadence, auxiliary review model, FTS tokenizer, snippet sizes, embeddings, and
hybrid ranking are **not current decisions**. They should remain deferred until
their dependent stages exist.

## 10. Small follow-up tickets and dependencies

1. **MEM-D03 — Define the curated-memory taxonomy and provenance contract**

   Depends on: MEM-R02, FILES-D02, TRANSCRIPT-D06.

   Deliverable: allowed/forbidden fact classes, file ownership, source-event
   reference format, correction/conflict rules, no-op definition, retention.

2. **MEM-D04 — Decide note-taker write consent and failure behavior**

   Depends on: MEM-D03.

   Deliverable: direct vs staged writes, visible receipt/revert expectations,
   high-impact categories if conditional, and user-turn behavior on MCP failure.

3. **FILES-I03 — Integrate the pinned official Filesystem MCP surface**

   Existing dependency remains: FILES-D02 and stable patient paths from
   TRANSCRIPT-I07. Add acceptance checks for exact root, stdio transport,
   traversal/symlink containment, and no transcript/media access. Prove the ADK
   client enforces the selected tool filter, including exclusion of `move_file`;
   otherwise mark the integration unavailable and do not claim operation-level
   least privilege.

4. **MEM-I04A — Implement structured note-taker outcomes**

   Depends on: MEM-D03, MEM-D04, FILES-I03.

   Deliverable: `no_op/add/update/correct`, source event IDs, deterministic
   processed event-ID set, idempotency key, no psychologist write capability.

5. **MEM-E05A — Evaluate consolidation precision and corrections**

   Depends on: MEM-I04A.

   Measure false saves, missed durable facts, correction success, conflicts,
   repeat-invocation idempotency, sensitive overcollection, latency, cost, and
   behavior across two explicitly configured model choices.

6. **MEM-I04B — Add change receipts or staged proposals if approved**

   Depends on: MEM-D04, MEM-E05A.

   Keep receipts free of duplicated raw prose where hashes/event IDs suffice.

7. **RAG-R01A — Prototype sourced keyword transcript retrieval**

   Depends on: MEM-R02, TRANSCRIPT-I07, product approval to begin RAG-R01.

   Read ADK only through public APIs; build a disposable local FTS index; return
   snippet, canonical event anchors, bounded context, cursor, and rebuild state.

8. **RAG-E02 — Evaluate multilingual keyword recall and privacy boundaries**

   Depends on: RAG-R01A.

   Compare tokenizer/query behavior on French, English, diacritics, Unicode,
   corrections, and cross-patient negative tests before any embedding work.

9. **RAG-R03 — Decide whether semantic/hybrid retrieval is justified**

   Depends on: RAG-E02 and measured keyword failure modes.

   Include local embedding options, deletion propagation, model configuration,
   storage duplication, and threat model; “Hermes/OpenClaw use search” is not
   sufficient justification.

10. **MEM-R04 — Design durable asynchronous consolidation, only if needed**

    Depends on: MEM-E05A and evidence that synchronous invocation is inadequate.

    Specify deterministic processed event-ID sets as watermarks, job identity,
    retries, crash recovery, consent, model routing, and reviewer transcript
    isolation. Do not use a daemon thread as the durable design.

## Conclusion

Hermes validates Psyclaw's lean direction: canonical conversations, derived
search, and curated long-term memory should be separate. Its most valuable
lesson is architectural restraint at those boundaries, plus the operational
invariants learned when a reviewer accidentally shared a real session.

The next Psyclaw work should not be “implement Hermes memory.” It should define
the smaller clinical memory contract, integrate the already selected official
MCP filesystem surface, and evaluate a synchronous note-taker against canonical
ADK events. Keyword retrieval comes later as a disposable, sourced projection;
semantic RAG and background dreaming come only after measured need.
