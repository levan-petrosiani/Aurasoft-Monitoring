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

## 2026-10-24 · 14. Pushed Phase 0; Phase 1 design

**What:** Pushed `38c7151`. Phase 1 = AI run summary (triage) in the CLI and the portal.
**Why this shape:**
- The model gets a **digest**, not raw `run.json`: only problems, their failures and response excerpts, skipped
  and passed ids, and warnings. That keeps prompts small, cheap and readable for local models.
- **Facts are computed in code**: counts, outcome changes vs the bank's previous run, scenario ids, clause refs.
  The model groups problems by cause and explains them.
- Its answer is **validated in code** before it is stored. A tester can't easily spot a made-up clause or scenario
  id, so the tool must.

## 2026-10-24 · 15. Phase 1: Claude request settings (Opus 5.5)

**What:** `AnthropicDefaultsChatClient` sends every Claude call through the **beta** endpoint with
`fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) and a per-feature **effort** (summary/ask `medium`,
drafting `high`, check `low`) via adaptive thinking. Options: `ApiTestTool:AI:Anthropic:RefusalFallback`,
`Features:<f>:Effort`.
**Why:** Our prompts contain security-testing material (signatures, tokens, OAuth flows), which can trip safety
classifiers; with the fallback, a declined request is re-run on Anthropic's recommended model instead of failing.
Opus 5.5 defaults to effort `medium`, so we set it explicitly per feature.
**Verified without a key:** `AnthropicRequestTests` captures the real HTTP request offline and checks the model,
`max_tokens`, `fallbacks: "default"`, the beta header, `output_config.effort` and `thinking: adaptive`.
**Still unverified:** a live Claude call (no `ANTHROPIC_API_KEY` yet).

## 2026-10-24 · 16. Phase 1: summarizer, storage, CLI

**What:** `RunDigest`, `RunSummary` (schema, parse, validate), `RunSummarizer`; table `run_summaries` (migration
`RunSummaries`); `aqt ai summarize <run-id|latest> [--provider] [--model] [--show-input]`.
**Why:**
- One repair retry when the answer isn't valid JSON (local models sometimes add prose).
- An unreadable answer is stored as `unstructured` with the raw text, never thrown away.
- Runs without results are summarized without a model call.
- Every summary links to its `ai_calls` row.
- JSON-schema response format is used for Ollama only. For Claude the schema is in the prompt, because the adapter
  plus fallback combination with structured outputs is untested here.

## 2026-10-24 · 17. Phase 1: two bugs found by the first real summary

1. **Masking hid XS2A error codes.** JSON key `code` was masked everywhere (meant for the OAuth code), so
   `"code":"FORMAT_ERROR"` became `***`, which is exactly the evidence a tester needs. Now `code` is masked only in
   form bodies and query strings. All 12 runs re-imported: error codes readable, still 0 secrets.
2. **Local model timeout.** OllamaSharp's HttpClient default is 100 s, and `gemma4:26b` needed longer. Added
   `Ollama:TimeoutSeconds` (600). Timeouts are now audited and stored as failed summaries, not treated as a
   user cancellation.

## 2026-10-24 · 18. Phase 1: local model choice (user's request)

**What:** Default local model changed from `gemma4:26b` (17 GB) to **`qwen3:8b`** (5.2 GB).
**Why:** The user asked for a smaller model. Same TBC run: gemma4 192 s, qwen3 35 s, both found the right root cause
and clause. qwen3 made more small mistakes, and those were fixed in code (step 19). The only installed models are
gemma4:26b and qwen3:8b; bge-m3 and the reranker are embedding models and can't summarize.

## 2026-10-24 · 19. Phase 1: evaluation on all stored runs, and fixes

**What:** Summarized all 9 runs with results using qwen3:8b and compared each answer with the database.
**Errors found → fix:**
- "Since previous run" text misdescribed changes ("previously failed" for Blocked) → the comparison is now
  **written in code** from the computed changes and removed from the model's schema.
- Warnings of passed scenarios reported as findings (PRE-002 untrusted cert on MockBank) → **code** moves
  findings whose scenarios all passed to "Worth a look".
- Random 503s of the TBC mock blamed on the bank → prompt: 502/503/504/timeouts are "environment" unless persistent.
- A tester clicking Deny (`access_denied`) blamed on the bank as Critical → prompt: mark "unclear", suggest re-run.
- Validator cleared a correct but shortened clause → it now matches clause numbers (6.3.1.2, 14.11.1) and
  stores the run's exact reference.
**Result:** Re-run on the 5 affected runs: all now correct against the data (sides, grouping, comparison).
Speed 15–35 s per run locally.

## 2026-10-24 · 20. Phase 1: portal panel and tests

**What:**
- **AI summary** panel on the Run page: "Summarize with Claude" / "Local (qwen3:8b)", health badge, findings with
  side, evidence, clause and next step, "since previous run", warnings, and the tool's corrections.
- Optional `ApiTestTool:AI:Summary:AutoSummarize`, queued after the run is saved.
- Tests: 90 total (engine 53, store 18, AI 19). The database fixture and sample run moved to `tests/Shared`.
**Verified:**
- Build clean, 90/90 tests pass.
- The portal renders the stored summary for the TBC run (checked in the server-rendered HTML).
**Not verified:** clicking the buttons in a browser, auto-summarize after a portal run, and a live Claude summary.

## 2026-10-24 · 21. Pushed Phase 1; Phase 2 plan

**What:** Pushed `efa3d00`. Phase 2 = standards → clauses → AI-drafted rules → human review.
**Why rules before tests:** a reviewer can check a one-line rule with a quote in seconds; checking a generated test
takes much longer. With approved rules as the brief, every Phase 3 test can be traced back to a sentence of the standard.

## 2026-10-24 · 22. Phase 2: getting and reading the documents

**What:**
- Berlin Group IG **1.3.16** (341-page PDF, CC BY-ND 4.0, free download). The official library links its files to
  `badopkt.myrdbx.io`; that looked odd, but the official page itself references that host for all 156 of its files,
  so it is their file server.
- Georgian guides from openfinance.ge (XS2A 0.8.RC3, JWS 0.5.RC1, IAA 0.8.RC1): **Georgian only**, confirmed.
- `standards/sources.yaml` records the URL, version, licence and SHA-256 of each. The files are git-ignored and
  re-downloaded with `aqt standards fetch`. A changed file is refused until someone reviews it.
**Reading:**
- **PDF** (PdfPig 0.1.16): the table of contents gives the clause list, and each entry is found in order in the body.
  This keeps references like "cp. 6.3.3" from being taken as headings. The TOC stops at 3 levels, so 6.3.1.2 was hidden
  inside 6.3.1 (22k chars). A second pass finds sub-headings, accepting only the expected next number. Result: 182
  clauses, 0 problems.
- **PDF defect:** the chapter 6 heading has a stray sentence glued in ("The payment data contained … JSON or
  XML."). Fixed with an explicit, reviewable `titles:` correction in the manifest, not a parsing hack.
- **Word**: heading styles; numbers computed like Word. **Decision checked against the official PDF:** the
  "Contents" heading counts as chapter 1 (numbered style). The document's own cross-references disagree with each other
  (some match, e.g. 7.6.2 and 8.3.2; some are stale, e.g. 6.6.1 and 7.2.3), so I downloaded the published PDF of the same
  version: it prints "1 სარჩევი", 7.6.1 PartyIdentification, 8.2.3, 8.3.2, matching my numbering.
**Language decision:** no whole-document translation. Georgian text is the source of truth and quotes are verified
against it. Each rule carries an English translation of its quote, marked as AI-made, because bulk translation is
costly and an 8B local model translates Georgian poorly.

## 2026-10-24 · 23. Phase 2: database and search

**What:**
- Tables `standard_documents`, `clauses`, `rules`, `rule_reviews` (migration `StandardsAndRules`) with pgvector.
- `clauses.search` is a **generated** tsvector: English stemming for English documents, plain words for Georgian
  (Postgres has no Georgian stemmer).
- HNSW indexes on the embeddings.
- Ingest: 379 clauses with local **bge-m3** embeddings in 31 s.
**Why hybrid search:** words find exact field names (validUntil, PSU-IP-Address); embeddings find meaning across
languages. Results are merged by reciprocal rank.
**Fixes during testing:**
- `websearch_to_tsquery` needs every word, so questions found nothing → words are joined with "or" and ranked.
- Ollama silently truncates prompts at its default context window → `num_ctx` 16k is set on every Ollama call.
  This also protects the Phase 1 summaries.
**Verified:**
- "how many times per day can the TPP read account data" (English) finds GE-XS2A 2.1.2 and 9.1.1.6 (Georgian).
- "consent validity" finds GE 9.1.1.10 თანხმობის ვადა.

## 2026-10-24 · 24. Phase 2: rule drafting

**What:**
- `RuleDrafter`: prompt with the clause, its section path and the existing rules for the clause; JSON answer with one
  repair retry.
- Code checks:
  - **quote must occur in the clause** (`QuoteVerifier`: ignores whitespace, line breaks, hyphenation, quote/dash
    style and case, nothing else; "…" splits into ordered parts; very short parts are refused). Otherwise the rule
    is stored as `invalid`, kept for the record but not reviewable.
  - **level vs keywords** (MUST/SHOULD/MAY and Georgian equivalents) → note for the reviewer.
  - **duplicates** → note.
  - ids `BG/GE-AREA-NNN`; a `drafted` history row per rule.
**Found by testing on real clauses (qwen3:8b):**
- BG 6.3.1.2: all quotes verified. The level check correctly questioned "MUST" on text without requirement keywords;
  global consent is actually optional ("if this function is supported"). Prompt now spells out optional = MAY.
  qwen3:8b still says MUST. That's an 8B-model limit, it's why drafting defaults to Claude, and the reviewer sees the
  warning.
- **False duplicate:** "accept `availableAccounts`" vs "accept `allPsd2`" scored 0.97 on embeddings. Duplicates now
  also need the **same identifiers** (fields, codes, statuses, paths), checked in code.
- GE 9.1.1.10 (Georgian): found the Georgia-specific **90-day consent limit → PERIOD_INVALID** and the 9999-12-31
  rule. Both quotes were verbatim Georgian and verified. Issues left for the reviewer: one rule combines two
  requirements, it adds a 400 status the text doesn't state, and the translation confuses TPP (მმპ) with ASPSP.
**Not verified:** drafting with Claude (no API key yet).

## 2026-10-24 · 25. Phase 2: review workflow and export

**What:** `RuleBook`: approve (drafts only), reject (reason required), edit (drafts only; an edited quote is checked
again; fixing the quote turns an `invalid` rule back into a draft), reopen, retire. Each action:
- needs a reviewer name,
- writes a `rule_reviews` row with the fields before and after,
- refuses if someone else changed the rule meanwhile.

`aqt rules export` / portal button writes approved rules to `rules/<prefix>/<id>.yaml` and removes stale ones.
**Decision changed:** the **database is the source of truth** for rules, with YAML as an export for git. The plan said
files first, but the queue, history and duplicate search need the database.

## 2026-10-24 · 26. Phase 2: portal pages

**What:**
- **Standards** page: documents, outline with rule counts, search (words + meaning), clause view with breadcrumb,
  "Draft rules with Claude / Local".
- **Rules** page: queue by status and standard; cards showing the quote (+ EN translation), check results colour-coded,
  approve/edit/reject/reopen, history line; export button.
- The reviewer types their name once and it's kept in the browser (no login yet).
**Bug found and fixed:** opening `/standards?clause=…` updated the URL during server prerendering, which became a 302
to the same page, an endless redirect loop in a browser. Now the URL only changes on a click.
**Verified:** all three pages return 200 and render real data (5 drafts, the Georgian clause with its breadcrumb).
Not clicked through in a browser by me.

## 2026-10-24 · 27. Phase 2: tests

**What:** New `ApiTestTool.Standards.Tests` covering:
- the TOC parser and wrapped entries, headings, header stripping and sub-sections
- Word numbering, including the numbered Contents heading, numbering turned off and skipped levels
- the quote verifier, English and Georgian
- the manifest and a hash mismatch

AI tests: level questions, identifier duplicates, drafting (numbering, invalid, level note, duplicate), the
review workflow and guards, stale review, export as valid YAML. Store test: hybrid search. **122 tests pass.**
**Two test bugs, not product bugs:**
- My test quote "corrected" the PDF's own typo ("unambigously"); the verifier rightly refused it.
- `jsonb` reformats JSON; the test now parses it.

## 2026-10-24 · 28. State after Phase 2

- In the database: 4 standards, 379 clauses (all embedded), 5 draft rules from the qwen3:8b trials, left for the user
  to review as a demo:
  - BG-CON-001..003: BG-CON-003 is wrong (MUST for an optional function), so reject it with a reason.
  - GE-CON-001..002: approve 002; edit 001 to remove the second requirement and the 400 status.
- Next: set `ANTHROPIC_API_KEY` and compare Claude's drafts on the same clauses. Then Phase 3 (scenarios from approved
  rules).

## 2026-10-24 · 29. Portal: themed form controls (user's request)

**What:** The user reported that input boxes looked bare on most pages.
**Cause:**
- The stylesheet styled only `<select>`, so text boxes and text areas used the browser default.
- My Phase 2 pages put the `field` class (meant for the label wrapper) on the controls themselves.
**Fix:**
- Global styles for every text input, textarea and select: surface background, line border, 8 px radius, muted
  placeholder, accent hover/focus ring, themed drop-down arrow, accent-coloured checkboxes, disabled state.
- `color-scheme: light dark` so native pop-ups follow the theme.
- `input` / `input-inline` classes on the controls.
- The rule edit form uses the same `label.field` layout as New run.
- Clause text in the normal font (the monospace font made Georgian hard to read).
**Verified:** headless-Chrome screenshots of Rules, Standards and New run in dark mode, and Rules in light mode.

## 2026-10-24 · 30. Review guide for non-specialist reviewers

**What:** `docs/ai/REVIEW-GUIDE.md` (glossary, what the tool already checked, five questions, decision table, worked
examples from the first five drafts) and a collapsible **How to review** box on the Rules page. Linked from the README,
`rules/README.md` and `CLAUDE.md`.
**Why:** the user, who reviews for now, asked how to decide. Staff will review later, and the checklist should sit where
the decision is made. Writing it down also corrected my earlier advice: BG-CON-001 and 002 have the same flaw as
003 (they describe request formats, not duties of the bank), so all three should be rejected, not just 003.

## 2026-10-24 · 31. C# replaced by the Python implementation (user's request)

**What:** removed the C# solution (`src/`, `tests/`, `ApiTestTool.sln`, `Directory.*.props`, `nuget.config`,
`dotnet-tools.json`) and added the Python implementation at the repository root: `aqt/` (engine, CLI, MockBank,
Flask portal), `tests/` (pytest), `pyproject.toml`, `requirements.txt`. README, CLAUDE.md and ARCHITECTURE.md now
describe the Python tool. YAML (`banks/`, `scenarios/`), `docs/`, `standards/`, `rules/` and `docker-compose.yml`
are unchanged.
**Why:** the user asked to change the C# code to the Python port.
**Not ported:** Store (PostgreSQL, masking, import), AI (summaries, audit log, rule drafting/review) and Standards
(fetch/ingest/search), with their CLI commands and portal pages. They remain in git history (`8abf9f6`) and are
listed in the README under "Moved from C#".
**Verified (from this repository, fresh venv):** `pip install -e .[test]`; `aqt validate` (18 profiles, 23 scenarios);
`pytest` 22 passed; `aqt run --bank MOCK --with-mock --sca-auto` 16 passed, 7 skipped; `aqt run --bank TBC-MOCK
--suite preflight,tbc --sca-auto` 11/11; portal on :5280, a MOCK run from New run completed 16 passed / 7 skipped
and served its report files. Before removing C#, the Python client passed 16/16 against the C# MockBank and the
C# client 16/16 against the Python MockBank.

## 2026-10-24 · 32. Portal: Aura Software logo instead of the ✓ mark (user's request)

**What:** the header's ✓ mark is now the Aura Software logo, `aqt/portal/static/aura-logo.png`, shown 30 px high.
It was made from the user's `White Aura software logo.png` (2172×724, 380 KB, white on transparent): cropped to the
drawing, faint specks (alpha < 32) cleared, scaled to 431×96 and made pure white (8.8 KB). `pyproject.toml` now ships
`portal/static/*.png`, so a non-editable install has the logo too.
**Why:** the user asked for the company logo in the top-left corner. The header follows the system theme and the logo
is white, so the `--logo-filter` variable shows it black on the light theme and white on the dark one.
**Verified:** headless-Chrome screenshots of the header in light and dark mode (1× and 2×) and at phone width;
`pytest` 22 passed.

## 2026-10-24 · 33. Portal: header logo 20% bigger (user's request)

**What:** `.brand-logo` in `aqt/portal/static/app.css` is now 36 px high instead of 30 px (about 162 px wide).
**Why:** the user asked for the logo to be 20% bigger. The PNG is 96 px high, so it stays sharp on 2× screens.

## 2026-10-24 · 34. Portal: more space between "aura" and "monitoring" in the logo (user's request)

**What:** `aqt/portal/static/aura-logo.png` (the user's `Aura monitoring logo with larger subtitle.png`, 2172×724) now
has 28 px more space between the two words: everything above row 480 moved up 14 px and everything below moved down
14 px. The canvas size is unchanged, so the header layout is the same.
**Why:** the user said the words sat too close together. Rows 464–495 of the original were empty apart from
near-invisible specks (alpha ≤ 3), so the split doesn't cut through any visible splatter.
**Verified:** before/after renders on the dark theme background; nothing clipped at the top or bottom edge.

## 2026-10-24 · 35. Before pushing: friends' Python port found; plan to port the AI layer

**What:** `git fetch` before pushing showed three new commits on `origin/main` from teammates:
- the C# implementation replaced by Python (`ca72efb`),
- the Aura logo (`53fe338`),
- a CSS tweak (`9bb7d32`).

The Python port covers the engine, CLI, MockBank and portal, but not the Store, AI and Standards layers (Phases 0–2).
**Why I stopped:** pushing my local commit (the system overview PDF, which describes the C# system) on top would have
documented software that is no longer on `main`. A trial rebase on a temporary branch showed only a `NOTE.md`
numbering conflict. The user confirmed Python is final and asked for the port. Work happens on branch
`ai-layer-python` from their `main`, so their commits stay untouched. My local PDF commit stays on local `main`
(not pushed).

## 2026-10-24 · 36. Port: environment, and a macOS bug in the Python port

**What:**
- Python 3.14 (Homebrew) venv via `uv`; their 22 tests pass.
- uv marks the editable-install `.pth` file *hidden* on this Mac, and Python 3.14 skips hidden `.pth` files.
  Workaround: `python -m aqt` from the repo root (documented in CLAUDE.md).
- **Bug:** `test_all_mock_suites_pass` failed every time on macOS, and a real `aqt run --bank MOCK` gave 15/16. The
  second SCA scenario couldn't reopen the OAuth callback port (TIME_WAIT), because the listener set only Windows'
  `SO_EXCLUSIVEADDRUSE`. Fixed with `SO_REUSEADDR` on POSIX, which still prevents two active listeners.
  Now 16/16 and 22/22.

## 2026-10-24 · 37. Port: store (`aqt/store`)

**What:** psycopg 3 + plain SQL instead of EF Core.
- Migration `0001` is the exact schema the C# tool created (from `pg_dump`). A database with the C# tool's EF Core
  history is recognised and kept, so **all existing data is reused**.
- Masking, mapper and importer were ported.
- The C#-style `Host=...;Port=...` connection string still works.

**Verified:**
- Python mapping vs what C# stored, for all 12 runs and 139 exchanges: **0 differences**.
- Reads, previous-run lookup, summaries, standards, rules and search all work on the existing database.

## 2026-10-24 · 38. Port: standards (`aqt/standards`)

**What:** Word reader, PDF reader (pdfplumber instead of PdfPig), quote verifier, manifest.
**Found:**
- **Georgian guides:** identical clauses, titles and text to C# (133/32/32).
- **Berlin Group PDF:** pdfplumber returns the page footer *last*, but the C# rule "drop the page up to the
  License-Notice line" assumed it came first, and found no headings at all.
  - Fix: page furniture is dropped by **pattern** (`pageFurniture` in `sources.yaml`) plus automatic removal of lines
    repeated at the top of many pages.
  - Rotated figure labels (single letters) are filtered out.
  - Result: the same 182 clauses in the same order. Prose is identical; tables and figures differ in places
    (median similarity 0.97). **Every existing non-invalid rule quote still verifies.**
- YAML read an all-digit SHA-256 as a number (a test exposed it); the manifest now reads hashes as text.

## 2026-10-24 · 39. Port: AI layer (`aqt/ai`)

**What:**
- `chat.py`:
  - Claude via the `anthropic` SDK 2.3: beta endpoint, `fallbacks: "default"`, adaptive thinking, effort per
    feature, streaming.
  - Ollama over HTTP: `num_ctx` 16k, JSON schema output.
  - `AuditingChat`: masking plus an `ai_calls` row per call, including failures.
- `summaries.py`, `rules.py` (drafter, RuleBook, export), `ingest.py`.
- Prompts and schemas are **extracted programmatically** from the C# sources into `aqt/ai/prompts/`, so there are no
  retyping errors.

**Improvements made while porting:**
- The drafting prompt now says never to join non-adjacent list items without "…". This was the cause of the 8
  invalid BG 4.1 drafts.
- The Georgian "optional" (*არასავალდებულო*) no longer counts as "mandatory". It contains both *სავალდებულო* and
  *ვალდებულ*; the C# version had this bug too, and a new test caught it.
- Findings about **skipped** scenarios are dropped instead of moved to "worth a look". Seen in a portal screenshot:
  skipped TBC scenarios were listed as worth a look.

**Verified:**
- The exact Claude request against a local fake Anthropic server: model, `max_tokens`, `fallbacks`, beta header,
  thinking, effort, streaming.
- Python digest = C# digest for a real run.
- Real summaries with `qwen3:8b`: correct.
- A real draft from GE-XS2A 9.2.2: one rule for review; one correctly rejected because the model changed one
  Georgian word in its quote (*სათაურის* → *სათავის*).

## 2026-10-24 · 40. Port: CLI and portal

**What:**
- `aqt/cli_ai.py`: `db`, `ai`, `standards`, `rules`, and `run --db`. Imports happen inside the commands, so the core
  tool works without the `ai` extra.
- Portal:
  - `ai_services.py`: background store writes, summaries, rules.
  - Hooks in their `RunService`: queued, started, scenario completed, finished, deleted, plus optional auto-summary.
  - Routes and Jinja templates for the AI summary panel, Standards, Rules (review, edit, export).
  - The reviewer name is kept in a cookie.
  - Their CSS already contained my styles.
- `aqt portal [--db | --no-db] [--auto-summarize]`.

**Verified:**
- A portal MOCK run: 16 passed, stored with 30 exchanges and 26 events.
- Summary button → a correct local summary.
- Screenshots of the Run, Standards and Rules pages.
- With the database down, CLI runs still exit 0 with a notice.

## 2026-10-24 · 41. Port: tests

**What:** pytest ports of the store, standards, AI and portal tests (`tests/test_store.py`, `test_standards.py`,
`test_ai.py`, `test_portal_ai.py`, helpers in `tests/ai_support.py`), including:
- a fake Anthropic server,
- throw-away databases,
- a test that a database created by the C# tool is recognised.

**Result:**
- **93 passed** with `AQT_TEST_DB`.
- Without it, 76 passed and 17 skipped.
- No test databases are left behind.

## 2026-10-24 · 42. Port: docs

**What:** README ("Store and AI layer" section; "Moved from C#" now says everything is ported),
CLAUDE.md (Python modules, commands, the macOS note), ARCHITECTURE.md (components with the AI layer, summary
and rules flows, store schema, test counts), PLAN status.

## 2026-10-24 · 43. System overview PDF for the Python system; publishing as a pull request

**What:** Regenerated `docs/overview/system-overview.pdf` (19 pages) from the earlier overview, converted to the Python
system. The converter fails if any expected sentence is missing, so nothing stays outdated by accident. It covers
packages and line counts, the Python stack, the port's verification, the new bugs, current numbers (14 runs, 19 summaries,
24 rules, 93 tests), the roadmap (merge the PR first) and the decisions (Python as a team decision).
**Why:** the user chose to regenerate it for Python and to publish the port as a **pull request**, so teammates see
the change to their `aqt/oauth.py` before it reaches `main`. The earlier local commit with the C# PDF (`18409b2`) was
dropped from local `main` (it only ever existed locally).

## 2026-10-25 · 44. Phase 3: scenarios drafted from approved rules

**What:**
- **Engine (no AI):**
  - `rules:` field on scenarios. The engine checks only the format, e.g. `GE-CON-001`.
  - `scenarios.load_text` and `check_templates`. `validate` now uses `check_templates` too.
  - `aqt/dryrun.py` (`MockDryRun`): runs one scenario on an in-process MockBank on free ports, with the MOCK profile and the headless SCA approver.
- **Store:** migration `0002_scenario_drafts.sql`, plus:
  - `ScenarioDraftRow`;
  - draft CRUD with an optimistic `updated_utc` check;
  - `link_rule_scenario`, which adds the scenario to `rules.scenarios` and writes a `rule_reviews` row with action `linked`;
  - `latest_outcomes`;
  - `store/coverage.py`.
- **AI:** `aqt/ai/scenario_drafting.py` and the prompt `prompts/scenario_drafting.txt`.
  - `ScenarioDrafter`: the prompt is the format reference, the templates file and two example scenarios. The answer is YAML in a fence.
    1. Code overwrites id, suite, severity, `specRef`, `origin` and `rules:`.
    2. The draft is loaded with the engine and the template names are checked.
    3. Requests may only go to the bank's XS2A API, as a relative path or a saved link.
    4. A dry run on MockBank.
    5. Errors or failures go back to the model, up to 3 attempts.
    6. Code notes when the scenario does not expect the rule's status or error code.
  - `ScenarioReview`:
    - approve writes `scenarios/<suite>/<id>-<slug>.yaml`, reloads the whole folder (and removes the file if it no longer loads), then links the rule;
    - reject needs a reason;
    - edit keeps the code-owned fields, then checks and dry-runs again.
- **CLI:** `aqt scenarios draft|drafts|show|approve|reject` and `aqt coverage`. `validate` warns about scenarios linked to rules that are missing or unapproved (with `--db`/`AQT_DB`; a store failure is only a notice).
- **Portal:** a **Drafts** page (generate with Claude or locally, YAML, dry-run table, MockBank-gap note, approve/edit/reject) and a **Coverage** page. Approved rule cards link to their scenarios.

**Why:** this is the plan's Phase 3: approved rule → generated scenario → checked on MockBank → a person approves → coverage goes up. As elsewhere, facts are in code: the ids and the link to the standard are never taken from the model, and MockBank, not the model, decides pass/fail. The model's only judgement is "this failure is a MockBank gap". It is accepted only for a failed expectation, not a tool error, and is shown to the reviewer.

**Result:**
- Live with local qwen3:8b on a throwaway DB with GE-CON-001 approved there, so the real DB was untouched. In 57 s and 2 attempts it produced `NEG-CON-009`: `validTo` = today+91d, expect 400 `PERIOD_INVALID`.
- MockBank answered 201 and the model reported a MockBank gap. That is correct: MockBank has no 90-day limit (a follow-up for MockBank, plan decision 6).
- Tests: 104 passed with `AQT_TEST_DB`; 81 passed and 23 skipped without it. The C#-baseline migration test now also expects the later migrations.

**Not done:** no scenario files were approved. That needs the user to approve AIS rules first, and the real DB has only PIS/COMMON approved rules.

## 2026-10-25 · 45. MockBank: 90-day consent limit (GE-XS2A 9.1.1.10)

**What:** MockBank now rejects a `validTo` more than `MaxConsentDays` (90) from today with 400 `PERIOD_INVALID`, and
shortens `9999-12-31` to today+90 (`aqt/mockbank/app.py`, option in `options.py`/`mockbank.settings.json`). There is a new
end-to-end test for 91 days, 90 days and 9999-12-31. The scenario-drafting "MockBank gap" test now uses a case MockBank
still accepts.
**Why:** step 44's live draft for GE-CON-001 found this gap (plan decision 6: MockBank grows with the approved rules).
**Result:** a re-run of the live local-model draft for GE-CON-001 passes on MockBank on the first attempt (39 s). Tests: 105 with
`AQT_TEST_DB`, 82 + 23 skipped without. The MOCK run is still 16/16.

## 2026-10-25 · 46. Phase 4: the ask-agent

**What:**
- **Chat** (`aqt/ai/chat.py`): tool calling for Claude and Ollama, using one provider-neutral message format (`Tool`, `ToolCall`, `ChatResult.as_message`). Claude's assistant blocks go back as is, because with adaptive thinking a tool loop must return its thinking blocks. The audit records tool calls and results.
- **Store:** migration `0003_ask_agent.sql`:
  - schema `agent` with views over runs, scenario/step results, exchanges (no headers or bodies), events, summaries, rules and clauses;
  - login role `aqt_agent`, which can only SELECT those views;
  - table `agent_questions`.
  
  `migrate()` sets the role's password from `AQT_AGENT_DB_PASSWORD` (dev default `aqt_agent_dev`). New store methods: `agent_query` (logs in as `aqt_agent`, one prepared statement, read-only, 5 s timeout), `list_runs`, `scenario_history` and the question CRUD.
- **Agent** (`aqt/ai/agent.py`, prompt `ask_agent.txt`): eight tools (list_runs, get_run, get_scenario_details, scenario_history, search_standards, read_clause, find_rules, sql_query) and at most 8 tool rounds. Code also:
  - gives the model NOW;
  - masks every tool result;
  - checks every run, scenario or rule id and clause key in the answer against the tool results (status `unverified` if an id never appeared);
  - saves each question with its trace.
- **CLI and portal:** `aqt ask "<question>"` / `aqt ask history`, and the **Ask** page, which answers in the background and shows the history with what was looked up.

**Why:** this is the plan's Phase 4: staff ask about logged events at any time. It uses an agent with query tools rather than vector RAG over logs, because counts, time ranges and "what changed" need queries.

**Security finding:** my first version ran the SQL as the main user with `SET LOCAL ROLE aqt_agent`. Probing showed the agent's SQL could switch back, with `set_config('role','aqt',true)` or with a second statement (`reset role; …`). Switched to a separate login role plus prepared single statements. Re-probed: the database now denies access to raw tables, bodies, `ai_calls`, writes, `pg_read_file`, role switching and multiple statements, and stops `pg_sleep` by timeout. A test covers this.

**Result:**
- Live with local qwen3:8b:
  - "Why did TBC fail on October 21, and has it recovered since?" Correct cause: the sandbox returned 503 `STATE_UNAVAILABLE` on PRE-003/004. The first try wrongly said there was no evidence of recovery, because it had limited its own search to before the 22nd. Fixed in code (`get_run` now lists the bank's later runs) and in the prompt ("never claim something did not happen without looking"); the re-run found the 22 October run.
  - "Which scenarios failed most often?" Two SQL queries; the answer matches the data (NEG-CON-005 2× on TBC).
  - 45–60 s per answer locally.
- Claude is untested live (no API key); the request shape, including tools, `tool_use` parsing and `tool_result` turns, is tested with the fake server.
- Tests: 114 passed with `AQT_TEST_DB`; 86 passed and 28 skipped without it.

## 2026-10-25 · 47. Phase 5: payment initiation (PIS)

**What:**
- **Read the standard first.** Georgian XS2A 0.8.RC3 chapter 8: 8.1 products (`aspsp` recommended, `domestic`/`foreign` mandatory), 8.2.1 table 4 (fields per product: never/mandatory → FORMAT_ERROR), 8.3 initiation (TPP-Rejection-NoFunds-Preferred), 8.5 status (fundsAvailable), 8.7 cancellation (204/202/405) and 8.10 lifecycle (RCVD must never be returned; intra-bank must reach ACCC). Error codes come from BG IG 14.11.2 (PRODUCT_UNKNOWN 404, CANCELLATION_INVALID 405, PAYMENT_FAILED 400).
- **MockBank:** `/v1/{service}/{product}` initiation, status, details, cancel and authorisations.
  - Lifecycle: ACTC → (SCA) ACCC for `aspsp`, or ACSP → ACSC after `SettlementSeconds` (2) for domestic/foreign.
  - Validation: the table-4 field checks, an IBAN check digit test, the amount format, and the insufficient-funds rejection.
  - Once the PSU has authenticated for a payment, its token is required.
  - OAuth now accepts `PIS:<paymentId>` (approve/deny on a payment page).
  - Its sandbox IBANs had wrong check digits; they are fixed (GE63…01, GE36…02).
- **Engine:** `poll:` on request steps (a new X-Request-ID and signature per attempt, cancellable; a timeout is a failure naming the last value) and `expect.jsonOneOf`. `execute_request` is split into one send per attempt.
- **Scenarios:** header set `payment`; bodies `payment.domestic|aspsp|foreign`, which take their accounts from the profile's `variables.pis`; suite `pis` (PIS-001…005); NEG-PIS-001…007. MOCK and `_example` declare `features.payments: [aspsp, domestic, foreign]` and `variables.pis`.
- **AI layer:**
  - The scenario-drafting prompt knows payments, `poll` and `jsonOneOf`, and uses PIS examples for PIS rules.
  - Coverage counts rules per service.
  - Code refuses AI drafts that move money without `requires: variables.pis.*`.

**Why:** this is the plan's Phase 5. Payments are the half of open banking that moves money, so they are gated twice: PIS scenarios run only where a profile names the bank's sandbox test accounts, and AI drafts are held to that in code.

**Found on the way:** the live PIS draft from BG-AUTH-004 (signing baskets) sent payment data to `signing-baskets`, so my first payment-path check missed it. The check now also triggers on `vars.pis` in the text and on `payment.*` body templates; tests cover all three cases. The model also correctly reported that MockBank has no signing baskets (404); that is now a documented non-goal.

**Result:**
- MockBank run: 28 passed, 0 failed (was 16). All 12 PIS scenarios passed on the first run, and PIS-001 polls through the settlement.
- TBC-MOCK skips the whole `pis` suite because it has no test accounts.
- Tests: 117 passed with `AQT_TEST_DB`; 89 passed and 28 skipped without it.

## 2026-10-25 · 48. Phase 6: monitoring

**What:**
- **Schedules:** `monitoring.yaml`, bound and checked in `aqt/monitor/config.py`:
  - `every` (≥5 m) or a daily `at`; enabled; `withMock`; webhook env; retention days;
  - a schedule that never ran waits for its time of day, so starting the portal doesn't fire every nightly run;
  - a missed time catches up once;
  - shipped schedules: MockBank nightly 02:00 (on) and the TBC mock every 6 h (off, because it calls an external service).
- **Unattended runs:** `ScenarioRunner.skip_sca_reason`. Scenarios with an SCA step are skipped with a reason, unless SCA is automatic (MockBank). This applies in the portal (`RunRequest.unattended/schedule`) and via `aqt run --unattended`.
- **Running schedules:** the portal's `MonitorService` ticks every 30 s; it never queues a schedule that's still running, and it runs retention daily. `aqt monitor run-due` does the same for cron/launchd.
- **Alerts** (`aqt/monitor/alerts.py`), after every saved run (CLI, portal, schedule) against the bank's previous run:
  - kinds: regression, fixed, bank_down (not repeated while the bank stays down, and not doubled as a regression), run_failed;
  - stored once per run/kind/scenario;
  - optional webhook (`AQT_ALERT_WEBHOOK`, Slack/Teams-style `{"text"}`), masked; the delivery result is recorded on the alert.
- **Trends:** pass-rate series per bank, flaky scenarios (≥2 switches), slowest endpoints (ids in paths normalised), and an inline-SVG sparkline with no libraries.
- **Retention:** `aqt db prune [--days]`, run daily by the monitor; results, summaries and alerts are kept.
- **Store and pages:** migration `0004` (`monitor_state`, `alerts`, `agent.alerts` view for the Ask agent); portal pages **Monitoring** and **Alerts** (acknowledge with a name); CLI `aqt monitor status|run-due|alerts|ack|trends`.
- **Masker:** now also hides `Bearer …`/`Basic …` written out in free text (error messages, alert texts, JSON string values), not only in headers.

**Why:** this is the plan's Phase 6 plus the retention item from §6. It completes "monitor everything": history was already stored; now the tool runs on its own, notices changes and says so. Alerts and trends are facts, so they're computed in code. Unattended runs must not wait 5 minutes for a login nobody will do, and `--sca-auto` stays MockBank-only.

**Found on the way:** the tests showed that a broken critical preflight check produced two alerts (bank_down plus a regression of the same scenario), and that a "Bearer token" in plain text wasn't masked. Both are fixed.

**Result:**
- `aqt monitor run-due --schedule mock-nightly` ran 28/28; no alerts against the previous MockBank run, which is correct; schedule state and retention were recorded.
- On real history the Monitoring page shows pass-rate lines for MOCKGE22 and TBCBGE22. Flaky: TBC PRE-003/004 (the 21 October outage) and an early MockBank AIS-002. Slowest: TBC consent status, 1.9 s on average, 8.7 s at most.
- Tests: 137 passed with `AQT_TEST_DB`; 105 passed and 32 skipped without it.

## 2026-10-25 · 49. Eval sets; first results; Claude live; fixes the evals found

**What:**
- **Eval sets** `evals/*.yaml`, run with `aqt ai eval` (`aqt/ai/evals.py`):
  - summaries: 4 runs with known causes;
  - rules: 4 real clause texts copied verbatim, including an informative clause where the right answer is "no rules";
  - scenarios: 4 approved rules, one of which MockBank does not enforce;
  - ask: 6 questions over a seeded dataset of runs, alerts, a clause and a rule.

  Each case runs the real feature in a temporary database (`TemporaryDatabase`, also used by the tests) and is scored in code, never by a model. Calls are audited in the real `ai_calls`. Reports go to `reports/evals/<time>-<provider>.md/.json`.
- **`.env` loader** in `aqt/cli.py`: the user put `ANTHROPIC_API_KEY` in `.env` (git-ignored). Existing variables win and values are never printed.

**Why:** the last open item of the plan (§7: an eval set per AI feature before staff use it). Facts in code apply to grading too.

**First results, local qwen3:8b:** summary 3/4, rules 3/4, scenarios 4/4, ask 4/6 (checks 90/96). Investigating the failures:
- **Summary, our expired certificate.** It found the right scenarios but blamed the bank: the prompt said "Failed = bank-side", and a failing local certificate check is Failed. Fixed in code and prompt:
  - the digest now marks localCertificates steps as "our own certificates, checked locally, no request to the bank";
  - the prompt now says that our certificate/signature problems are our-side.

  The case was also unrealistic (the step had no `localCertificates` kind). It then passed 3/3 on the side check.
- **Eval case errors, mine.** Two cases forbade findings for *blocked* scenarios, which the prompt explicitly allows. Corrected. The certificate case accepted only `broken`, but Claude's `inconclusive` is right by the prompt's own definition; it now accepts both.
- **Rules, payment cancellation.** In a table of status codes the model rebuilt rows as "კოდი 202: …" ("Code 202: …"), text that does not exist, so the verifier (which stays strict) rejected 3 rules. A prompt rule on table quotes did not help the small model. Fixed in code: `RuleDrafter` now sends quotes that fail the verifier back **once**, with the verifier's reason ("copy exactly or remove"), and the answer is checked again. Passed live locally; tested.

**Claude** (`claude-opus-5-5`, first live calls, beta endpoint with the refusal fallback): `aqt ai check` OK in 2 s. Briefly, on the 4 cases the local model failed: all 4 pass (after the `inconclusive` correction), in 6–43 s each against 30–250 s locally.

**Decision (user: "maybe drop the local model if it's failing because it's weak"):** every feature already defaults to Claude. The local model stays as an opt-in only (offline use, or when no data may leave the machine), and its weaknesses are now documented by the evals. Ollama itself stays, for the `bge-m3` embeddings behind clause and rule search. Nothing was deleted.

**Tests:** 147 passed with `AQT_TEST_DB`.

## 2026-10-25 · 50. Claude vs the local model, measured; overview PDF regenerated

**What:**
- Ran every eval case with Claude (`claude-opus-5-5`) and with the local model (`qwen3:8b`), same cases and prompts.
- Regenerated `docs/overview/system-overview.pdf` (26 pages, same style). It now covers Phases 3–6, the eval sets and a new chapter 13, "Evaluation: Claude vs the local model" (chart, per-feature and per-case tables, findings, decision).
- Chart colours: Claude orange, local model blue (user's choice). The pair passed the dataviz palette validator for colour-blindness and contrast.

**Results (one full run each):**
- **Claude: 18/18 cases, 93/93 checks, 217 s in total.**
- **Local: 14/18 cases, 85/91 checks, 793 s:**
  - summaries 4/4 (the certificate fix holds);
  - scenarios 4/4;
  - Ask 5/6 (did not name rule GE-CON-001);
  - rules 1/4 (missed duties, a quote still wrong after the repair round). Its first run passed 3/4 rule cases, so the local model varies a lot between runs.

**Found on the way:**
- **Two parallel evals collided** on the shared `aqt_agent` role ("tuple concurrently updated") while creating temporary databases. Migrations now retry that specific conflict; 12 databases created in parallel succeed. One Claude case had errored this way, before the model was called; it was re-run.
- **One eval case was wrong:** payment cancellation (8.7) required "a rule about Authorization". Claude explained that the clause's last sentence is a TPP duty, not a bank duty, which the drafting prompt says not to turn into a rule. Claude is right. The case now requires the 405 rule instead.
- **The Ollama server crashed under load** during the local run (connection refused). The affected local features were re-run alone and merged (`merge_reports`-style: re-run cases replace errored ones).

**Decision:** unchanged and now measured. Claude is the default; the local model stays opt-in (offline, no data leaving the machine).

## 2026-10-25 · 51. Merged into main

**What:** merged `ai-layer-python` (the Python store, standards and AI layers) and then `phase3-scenarios` (Phases 3–6, eval sets, model comparison, overview PDF) into `main`, each with its own merge commit as the pull requests would have. Neither needed conflict resolution: `main` had not moved since the branches were cut. The README quick start no longer mentions the branch.
**Why:** the user asked for the pull requests to be merged so teammates get everything by pulling `main`. `gh` is not installed, so the merges were done in git and pushed; GitHub marks pull requests whose commits are in `main` as merged.

## 2026-10-25 · 52. Hackathon guidelines: cross-bank comparison, latency, portal and SCA guardrails, licences

**Why:** the hackathon's evaluation guidelines (Open Banking track) ask for four test layers (standard compatibility,
performance and reliability, latency, security), a cross-bank comparison shown on screen, reports that explain
deviations, visible guardrails, and a README with setup, architecture, third-party licences and guardrail evidence.
Checking the repository against them showed four gaps: no latency figures beyond a per-request "slow" warning, no
cross-bank view (`aqt compare` was on the "not done" list), a login-less portal open to cross-site form posts, and
`--sca-auto` enforced for real banks only in the portal, not the CLI.

**What:**
- `aqt/benchmark.py` (no AI, no DB): reads saved runs, maps every request to an operation (`GET /accounts/{id}/balances`;
  host, `/0.8`, `/v1` and ids removed, so TBC's and MockBank's URLs match), and computes per bank over its last N runs:
  pass rate, p50/p95/max latency per operation, 5xx or no answer, slow responses, the latest run's deviations with the
  first failure and clause, and per scenario "passed k of n" to show whether results are reproducible.
- `aqt compare [--bank A,B] [--last N]` (text) and the portal **Compare** page (SVG chart: p50 bar + p95 line per bank,
  hover for max and samples; tables). Bank colours are fixed slots of the validated categorical palette, and a bank
  keeps its colour when others are filtered out.
- Ran the public TBC sandbox 3 more times: 5 of 5 runs passed 11/11; TBC p50 323 ms, p95 559 ms over 114 requests.
- Portal: `only_from_this_portal` refuses POSTs whose `Origin`/`Referer` is another site (CSRF) and any request whose
  `Host` is not a local name (DNS rebinding). Forms from the portal itself, curl and tests (no Origin) still work.
- `allows_headless_sca` moved to `aqt/oauth.py` and is now used by the CLI too: `aqt run --sca-auto` on a real bank
  profile exits with code 2 and explains why.
- README: links to the architecture and overview at the top, a **Guardrails** table (risk → control → file) and a
  **Third-party components and licences** table (licences read from the installed packages' metadata), Compare page and
  command, refreshed verification numbers. CLAUDE.md and ARCHITECTURE.md (new section 5f) updated.

**Tests:** 154 passed with `AQT_TEST_DB` (7 new: operations, percentiles, comparison and flaky detection, CLI and portal
page, cross-site posts, DNS rebinding, `--sca-auto` refusal); 118 without a database. MOCK run 28 passed, 7 skipped.

## 2026-10-25 · 53. Pitch deck and demo video script

**What:** a 10-slide pitch deck (a Slides artifact, outside the repository) following the guidelines' pitch structure,
and `docs/demo-script.md`: a timed voice-over script for the 3-minute demo video (persona, storyboard per time slot,
preparation, recording tips).
**Why:** the deck and the video are the two required submission items, due 25 October 23:59. The demo needs a
deviation to explain, and MockBank passes everything by default, so the script plants three through MockBank settings
(`MockBank__VerifySignatures=false`, `MockBank__MaxFrequencyPerDay=10`). Checked from the CLI on a spare port: 25
passed and 3 failed (NEG-CON-004, NEG-SIG-001, NEG-SIG-002), with the reasons the script quotes. All figures in the
deck and script come from today's runs, the database and the evals; the manual-baseline comparison is left as a
placeholder because it was never measured.

## 2026-10-25 · 54. Hosting the portal: jury mode, Dockerfile, data copy

**Why:** the hackathon accepts an optional test URL with credentials. A teammate will host the portal on Railway, and
the portal was built for localhost only: it listened on 127.0.0.1, refused other host names, and had no login, so
anyone with the link could approve rules or spend the Claude key.

**What:**
- `aqt/portal/access.py` (`PortalAccess`), **jury mode**:
  - public host names in `AQT_PORTAL_HOSTS` (anything else is still refused as possible DNS rebinding);
  - HTTP Basic login `AQT_PORTAL_USER` (default `jury`) / `AQT_PORTAL_PASSWORD`, compared in constant time;
  - AI actions started from the portal (summaries, Ask, rule and scenario drafts) capped per UTC day
    (`AQT_PORTAL_AI_PER_DAY`, default 20, answered with 429 when used up);
  - deleting runs turned off;
  - a yellow "Hosted demo · synthetic data only" bar with the AI actions left.
- `aqt portal --host` (default 127.0.0.1) and `$PORT`. A public `--host` without jury mode is refused, so hosting cannot
  silently drop the login.
- `Dockerfile` (Python 3.12, `.[ai]`, `aqt db migrate` when `AQT_DB` is set, then the portal on 0.0.0.0) and a
  `.dockerignore` that keeps `.env`, certificates, reports and downloaded standards out of the image.
- README "Hosting the portal": variables, Railway steps, and copying the local data (data-only `pg_dump`, restored
  after the first deploy has created the schema). CLAUDE.md and ARCHITECTURE.md updated.

**Checked end to end:** built the image and ran it in jury mode against a throwaway database:
- migrations applied; every page answered 401 without the login, 200 with it, and 403 for another host name;
- the data copy restored 25 rules, 20 runs and 379 clauses with their embeddings. Its one error was `monitor_state`,
  which the new portal had already written, so the dump command now leaves that table out;
- MOCK (28 passed, 7 skipped) and the TBC sandbox (11/11) both ran inside the container, and the hosted Compare page
  showed both banks.

The test container, database, dump and image were removed afterwards.

**Not on the hosted copy:**
- Ollama: clause search and ingesting standards do not work there.
- Manual SCA: it needs the tester's own machine.

**Tests:** 158 passed with `AQT_TEST_DB`, 4 of them new (password required for a public portal; host, login and
delete rules; the daily AI cap; `--host 0.0.0.0` refused without jury mode). 122 without a database.

## 2026-10-25 · 55. Deck review fixes; PRE-003 cites RFC 8705

**What:** applied an external review of the pitch deck (in the Slides artifact, not the repository).
- Slide 2 now leads with a fix-it example: TBC savings accounts have no `_links` while TBC's loans do. It shows the
  impact on TPPs, the standard and the fix. The two discovery warnings cite RFC 8414 §2 and RFC 8705 §5.
- Removed the overclaim that "404 vs 403" and "numbers vs strings" were found by the tool: those were noticed by hand,
  and no test asserts them.
- Slide 7: "3 deviations flagged" next to "11/11 ran end to end", plus a Compare screenshot.
- The Challenge Book's names (Auditor Tool, Incompatibility Report, Quality Report) are used on slides 3, 4 and 7.
- Security examples are marked "on MockBank" (TBC's mock ignores signatures).
- "Judgement by the model" is now "drafts and explanations by the model".

PRE-003's `specRef` now names RFC 8705 §5, so the deck's citation for `mtls_endpoint_aliases` is backed by the
scenario.

**Found on the way:** a portal run at 13:53 ran *all* suites against TBC-MOCK. The generic negative tests NEG-CON-005
and NEG-CON-006 got `FORMAT_ERROR` where they expect `PARAMETER_NOT_SUPPORTED`, and NEG-CON-008 got 404 where it
expects 401. These are not yet reviewed as TBC deviations, because those tests encode our reference behaviour, so the
deck does not use them. Compare shows that run as TBC's latest (85.7%); the deck's screenshot is taken from the
targeted CLI runs only.
