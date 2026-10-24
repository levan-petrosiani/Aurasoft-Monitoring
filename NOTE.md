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
