# NOTE.md — work journal

What was done at each step and **why**. Newest entry at the bottom. One entry per step:
date · what · why · result / follow-ups.

---

## 2026-10-24 · 1. Analysed the existing project

**What:** Read the README, solution layout, engine, CLI, portal, MockBank, profiles and scenarios.
**Why:** To know what's already there before adding AI to it.
**Result:** A working XS2A test tool for AIS (preflight, negative, ais, tbc suites), with a portal and a MockBank.
It stores results only as files and has no PIS yet. Most bank profiles are still placeholders.

## 2026-10-24 · 2. Evaluated the AI ideas and wrote the plan

**What:** `docs/ai/PLAN.md`, `CLAUDE.md`, `rules/README.md`, `rules/_example.yaml`, `standards/README.md`.
**Why:**
- *Document → rule → scenario* instead of document → scenario: a human reviews a rule with a clause
  reference, and the scenario is derived from it, so every test can be traced back to the standard.
- *The AI writes and the engine runs:* results stay reproducible and auditable. The model never calls bank APIs.
- *An agent with SQL tools instead of plain vector RAG over logs:* questions about logs are mostly counts and
  time ranges, which similarity search answers badly. Retrieval is used only for the standards text.
- *A code-checked verbatim quote on every rule:* catches clauses the model made up.
**Result:** The user approved the approach.

## 2026-10-24 · 3. Recorded the user's decisions

**What:** Updated PLAN.md §8 and CLAUDE.md with the decisions.
**Why / decisions:**
- **Database: PostgreSQL** (user's choice), not SQLite. Runs in Docker for development (`docker-compose.yml`
  will be added in Phase 0). Full-text search uses Postgres `tsvector`. `pgvector` is kept for semantic clause
  search later.
- **Documents: latest versions, in English.** Berlin Group NextGenPSD2 Implementation Guidelines **v1.3.16**
  (2 Dec 2025) + OpenAPI files. Georgian standard from openfinance.ge (the NBG page links there): XS2A Implementation
  Guide 0.8.RC3, JWS Guide 0.5.RC1, IAA Guide 0.8.RC1, Identity Assurance 0.3.RC3, plus the NBG Rule on
  Inclusion in Open Banking (80/04, English).
  ⚠ openfinance.ge lists its standards as **Georgian**, and the web version shows "Coming Soon". Before Phase 2
  we have to find out whether English versions exist (ask obstandards@association.ge). If they don't, see the
  translation note in PLAN.md §8.
- **Approvers:** the team for now, real staff later. A simple reviewer name now, so roles can be added later
  without changing the data model.
- **Scope:** AIS first, PIS designed in from the start (see PLAN.md §9).
- **Context:** this is a hackathon project, so the phases are ordered for a working demo at each step.

## 2026-10-24 · 4. Answered "can we use a local model instead of the cloud?"

**What:** Checked the machine: Apple M4 Pro, 24 GB RAM, Ollama installed with `gemma4:26b`, `qwen3:8b`, `bge-m3`
(embeddings) and `bge-reranker-v2-m3`.
**Why / conclusion:** Use a **hybrid behind one switch**, not local-only:
- **Rule and scenario drafting → Claude.** The inputs are public standards documents, so there's no data-policy
  issue at all. These are the hardest tasks (exact quotes, valid YAML in our format, several rounds of fixes),
  and a 26B local model is much weaker and slower at them. Weaker drafts mean more human review time.
- **Run summaries and ask-the-agent → configurable: Claude or local (Ollama).** These are the only features that
  see run data. Sandbox data is test data, and it's masked before sending anyway. Still, the local option lets
  us show that "run data never leaves the machine" when a bank asks.
- **Embeddings for clause search → local `bge-m3`** (Anthropic has no embeddings API, and bge-m3 is good and free).
- Seam: summaries and the agent are written against `Microsoft.Extensions.AI` `IChatClient`. The Anthropic C#
  SDK and OllamaSharp both implement it, so changing provider is a config change. Rule drafting uses the
  Anthropic SDK directly, because it needs citations and structured output.

## 2026-10-24 · 5. Created ARCHITECTURE.md and this journal; initialised git

**What:** `ARCHITECTURE.md` (Mermaid diagrams of the current system), `NOTE.md`, `git init`, and IDE folders,
`standards/index/` and `.env` added to `.gitignore`.
**Why:** ARCHITECTURE.md is a snapshot that gets updated after each phase. git lets AI-drafted files be
reviewed as diffs. Nothing is committed yet.
**Follow-ups:**
- **No .NET SDK on this Mac** (`dotnet` not found; the project was built on Windows). It must be installed before
  Phase 0 can be built and tested here (e.g. `brew install --cask dotnet-sdk`; it needs .NET 10).
- Initial commit of the baseline, once the user confirms.

## 2026-10-24 · 6. Baseline commit and push to GitHub

**What:** Committed the current state on `main` (147 files) and pushed it to
github.com/levan-petrosiani/Aurasoft-Monitoring (public, confirmed by the user).
**Why:** Checked before committing: no certificates, keys or passwords are in the tracked files (`certs/`,
`reports/`, `publish/`, `bin/`, `obj/` and IDE folders are ignored). The commit author is set in this repo only to
the user's GitHub no-reply address (`188484031+levan-petrosiani@users.noreply.github.com`). That links commits to
the GitHub account without publishing a real email address. The Mac's default (`levan@Levans-MacBook-Pro.local`)
would have shown as an unknown author.
**Also decided:** keep C#, don't port to Python. Reasons: the code works and is tested (47 tests); the
signing code was cross-checked against AltaSoft's production C# crypto; every AI piece we need has an official
or mature .NET package (Anthropic SDK, Microsoft.Extensions.AI, OllamaSharp, Npgsql, pgvector); and a port
would use up the hackathon time. A small Python helper stays an option for PDF parsing only, if .NET parsing
turns out poor.

## 2026-10-24 · 7. Git identity and .NET SDK check

**What:** Set this repo's commit email to `levanpetrosiani8@gmail.com` (user's choice); the remote stays on SSH
(`git@github.com:levan-petrosiani/Aurasoft-Monitoring.git`). Checked the .NET install: SDK **10.0.402**,
`dotnet build` 0 warnings / 0 errors, `dotnet test` **53/53 passed**, `aqt validate` all OK (23 scenarios),
full MOCK run with the built-in MockBank **16 passed, 0 failed, 7 skipped** (TBC-only scenarios).
**Why:** Proves the baseline works on macOS before Phase 0 changes anything, so any later failure comes from our
changes. The already-pushed first commit keeps the no-reply author: rewriting it would need a force push, and
both addresses link to the same GitHub account.
**Note:** `dotnet` printed "An issue was encountered verifying workloads"; this is harmless for us (we use no
workloads). `dotnet workload update` clears it. README still says 47 tests; there are 53 now.

## 2026-10-24 · 8. Phase 0: PostgreSQL in Docker

**What:** `docker-compose.yml` runs `pgvector/pgvector:pg17` as `aqt-postgres` on **localhost:5435** (db/user `aqt`,
dev password `aqt_dev`).
**Why:** Port 5432 is taken by the Homebrew Postgres, 5433 and 5434 by other containers. Bound to 127.0.0.1 so it
isn't reachable from the network. The pgvector image now, so Phase 2's vector search for clauses needs no new
image. The password is a local default only, and the compose file says so.

## 2026-10-24 · 9. Phase 0: store project (`ApiTestTool.Store`)

**What:** EF Core + Npgsql, snake_case tables `runs`, `scenario_results`, `step_results`, `exchanges`, `events`,
`ai_calls`; migration `Initial`; `dotnet-ef` installed as a local tool (`dotnet-tools.json`).
**Why:**
- *EF Core migrations* so schema changes in later phases are versioned and repeatable.
- *snake_case* because people and the Phase 4 ask-agent will query it with SQL.
- *Run id = report folder name*, so a database row and its folder always match and an import is repeatable.
- *Events and AI calls are not deleted with a run*: deleting a run in the portal shouldn't erase the history
  of what happened.
- *Pinned `Microsoft.EntityFrameworkCore.Relational` 10.0.13*: Npgsql alone resolved EF 10.0.4, the Design package
  10.0.13, and the CLI failed with CS1705 (assembly version mismatch).

## 2026-10-24 · 10. Phase 0: masking (`SensitiveDataMasker`)

**What:** Masks `Authorization`/`Proxy-Authorization`/`Cookie`/`PSU-ID` headers; `access_token`, `refresh_token`,
`id_token`, `code`, `code_verifier`, `client_secret`, `password` in JSON (any depth), form bodies and query strings;
IBANs (keeps `GE29` + last 4). Caps bodies at 64 KB.
**Why:** The engine already shortened tokens in reports, but it kept the first 12 characters. The first import
showed 15 such prefixes in the database, so the store layer now masks the whole value. This one class is used for
both the database and anything sent to a model.
**Verified:** SQL search over all imported exchanges: 0 tokens/codes in any form; Authorization is `Bearer ***(masked)`.

## 2026-10-24 · 11. Phase 0: import and wiring into CLI and portal

**What:**
- `aqt db migrate|import|status`; `aqt run` saves when `--db`/`AQT_DB` is set.
- The portal writes runs and events through `StoreRecorder`: a background queue, one write at a time. It
  migrates on startup in Development (`ConnectionStrings:Aqt` in `appsettings.Development.json`).
- The importer reads `summary.json` folders and also portal folders that only have `run.json` (cancelled runs).
**Why:**
- A background queue, so a slow or stopped database never delays a run or the UI.
- Cancelled runs are imported too, because "store every data" includes runs that never produced results.
- Existing history is now in the store: **12 runs, 90 scenario results, 139 exchanges**.
**Bug found and fixed:** with the database down, `aqt run` crashed with `RetryLimitExceededException` (EF's retry
wrapper, which my exception filter didn't list). The CLI save now treats any store error as a notice. Verified:
run exit code 0 with the database down; `db status` gives a clean error with exit code 2.

## 2026-10-24 · 12. Phase 0: AI skeleton (`ApiTestTool.AI`)

**What:**
- `AiOptions`: per-feature provider and model. Defaults are Claude `claude-opus-5-5` for every feature;
  Ollama's default model is `gemma4:26b`.
- `ChatClientFactory`: Anthropic SDK `AsIChatClient()` or OllamaSharp, both as `IChatClient`.
- `AuditingChatClient`: masks outgoing text and writes one `ai_calls` row per call, including failed and
  streamed calls.
- `aqt ai check`.
**Why:** Features will only see `IChatClient`, so moving summaries or the ask-agent to a local model is a config
change (decision in step 4). Auditing in a wrapper means no feature can forget it.
**Verified:**
- Ollama `qwen3:8b` answered (≈6 s) and was audited with token counts.
- Claude without a key failed with a clear message, and that failure was audited too.
- I removed these test rows (and a test MOCK run whose report folder was temporary) so the database holds only
  real history.
**Note for Phase 1:** Opus 5.5 specifics (effort level, server-side refusal fallback) are not set through
`IChatClient` yet. Phase 1 sets them when the summary feature makes real calls.

## 2026-10-24 · 13. Phase 0: tests and checks

**What:** New test projects `ApiTestTool.Store.Tests` (masking, mapping, import, PostgreSQL round trips) and
`ApiTestTool.AI.Tests` (provider choice, masking and audit, failed and streamed calls).
**Result:** 75/75 pass with `AQT_TEST_DB` set (53 engine, 17 store, 5 AI). Without it, the 3 database tests are
skipped, not failed. Each database test class creates and drops its own database, and none are left behind.
End to end:
- `aqt run --bank MOCK --with-mock --sca-auto --db ...` → 16 passed and saved with 30 exchanges and a
  `run.finished` event.
- The portal starts, migrates and serves pages.
**Not verified by me:** a run started from the portal UI (Blazor needs a browser). Check: start a MOCK run in the
portal, then `aqt db status` should show one more run, with `run.queued/started/completed` events.
