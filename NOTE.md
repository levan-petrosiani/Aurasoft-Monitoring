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
