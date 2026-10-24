# AI layer — plan

Status: Phases 0–6 done (Phases 3–6 on 2026-10-25), ported from C# to Python (NOTE.md steps 35–42). Open: eval sets for the AI features. Live Claude calls are not verified yet (no API key).
something demoable. Progress is logged in `NOTE.md`; `ARCHITECTURE.md` is updated at the end of each phase.

## 1. Goals

1. AI writes test cases from the Berlin Group (NextGenPSD2) and Georgian (NBG / openfinance.ge) standards.
2. AI summaries of runs.
3. AI drafts **rules** from the standard, with clause references; a human approves each rule before it goes live.
4. Monitoring: store every run and event.
5. Staff can ask an agent questions about runs and logged events at any time.
6. AIS first; PIS must fit the same design (§9).

## 2. Evaluation of the ideas

| Idea | Verdict | Change proposed |
|---|---|---|
| AI writes test cases from the docs | Good fit: scenarios are YAML in a small format, so the model writes data, not code, and `aqt validate` + MockBank can check it mechanically | Go **document → approved rule → scenario**. The rule is the unit a human reviews, and scenarios are derived from it |
| AI "tests different APIs" | Risky if the model calls bank APIs itself: results can't be repeated or audited | The model **writes** scenarios; the deterministic engine **runs** them |
| AI summaries | Quickest win; `run.json` already has the data | Make it *triage*: group failures by cause, say whether the bank or our side is at fault, cite the clause, compare with the bank's previous run |
| Rule drafting + human approval | The strongest idea | Rules as files (`rules/*.yaml`) with status, verbatim quote and reviewer; quotes checked in code |
| Store every data | Needed, but the data includes tokens, PSU data and IBANs | Mask before writing, keep a retention period, keep an audit log of AI calls |
| RAG agent over logs | Plain vector RAG answers count and time-range questions badly | An **agent with query tools** over PostgreSQL; retrieval only for the standards text |

## 3. Target pipeline

```
 standards/ (BG IG 1.3.16 + OpenAPI, Georgian XS2A/JWS/IAA guides)
      │  ingest: clauses with stable IDs → PostgreSQL (tsvector + pgvector/bge-m3)
      ▼
 clause index ──► AI drafts rules ──► human review (portal) ──► rules/*.yaml (approved)
                                                                    │
                                     AI drafts scenarios ◄──────────┘
                                            │ validate → dry-run on MockBank → human review
                                            ▼
                                    scenarios/*.yaml ──► engine runs ──► PostgreSQL event store
                                                                              │
                                    AI run summary / triage ◄─────────────────┤
                                    Ask-the-agent (tools: store + clauses) ◄──┘
                                    Coverage matrix: clause → rules → scenarios → last result (AIS | PIS)
```

## 4. Architecture

New projects (public NuGet packages only):

| Project | Contents | Packages |
|---|---|---|
| `ApiTestTool.Store` | PostgreSQL store: runs, scenario/step results, exchanges (masked), events, AI audit log, rule reviews, clauses. EF Core migrations | `Npgsql.EntityFrameworkCore.PostgreSQL`, `Pgvector.EntityFrameworkCore` (Phase 2) |
| `ApiTestTool.Standards` | Ingest PDF/DOCX/OpenAPI → clauses; clause search (full-text + vector); quote verification | `PdfPig` / `DocumentFormat.OpenXml` |
| `ApiTestTool.AI` | Features: `RunSummarizer`, `RuleDrafter`, `ScenarioDrafter`, `OpsAgent`; provider selection; masking; audit | `Anthropic`, `Microsoft.Extensions.AI`, `OllamaSharp` |
| Portal additions | Run summary panel; pages: Standards, Rules (review queue), Drafts, Ask, Coverage | — |
| CLI additions | `aqt db migrate`, `aqt db import`, `aqt ai summarize <run>`, `aqt standards ingest`, `aqt ai draft-rules`, `aqt ai draft-scenarios`, `aqt ask "..."` | — |
| `docker-compose.yml` | `pgvector/pgvector:pg17` on localhost:5435; connection string `ConnectionStrings:Aqt` / env `AQT_DB` | — |

The engine gets only small changes with no AI in them: `rules:` and `service:` on `Scenario`, and a result
sink so finished runs go to the store. Without a database configured, CLI runs keep working (files only),
so the tool stays usable for the standalone case.

### Model providers

| Feature | Provider | Why |
|---|---|---|
| Rule drafting | Claude (`claude-opus-5-5`), Anthropic SDK directly | Public documents only. Needs citations, structured output and the best accuracy |
| Scenario drafting | Claude (`claude-opus-5-5`), tool loop | Several rounds of draft → validate → MockBank; quality cuts review time |
| Run summary | `IChatClient`: Claude by default, Ollama (`qwen3:8b`; `gemma4:26b` optional) by config | Sees run data, so the local option keeps data on the machine |
| Ask-the-agent | `IChatClient` + function calling: Claude by default, Ollama by config | Same reason; Claude is more reliable at multi-step tool use |
| Clause embeddings | Ollama `bge-m3` (local) | Anthropic has no embeddings API; bge-m3 is free and good |

Config: `ApiTestTool:AI:{Feature}:Provider = anthropic | ollama`, `Model`, `ApiTestTool:AI:Ollama:Url`.
Key: `ANTHROPIC_API_KEY` from the environment. Prompt caching for the stable part of prompts (standard text,
DSL reference, templates).

## 5. Rule and approval model

- Format: `rules/_example.yaml`. ID `<STD>-<AREA>-<NNN>`; `service: AIS | PIS | COMMON`.
- Lifecycle: `draft` → `approved` | `rejected`; `approved` → `retired` (superseded by a new standard version).
- Only the portal review action sets `approved`, recording reviewer, time and note in the file and in `rule_reviews`.
  Reviewer is a name chosen in the portal now (the team); later this comes from a real login with an
  `Approver` role. The data model already has the reviewer field, so nothing changes.
- Code checks before a draft reaches the queue: verbatim quote found in the clause; operation/headers exist in
  templates or the OpenAPI spec; similar existing rules shown side by side.
- Scenarios list `rules: [...]`. `aqt validate` warns about references to draft/rejected rules and approved
  rules with no scenario.
- New standard version → re-ingest → rules whose clause text changed are flagged "needs re-review".

## 6. Event store and monitoring (PostgreSQL)

- Tables: `runs`, `scenario_results`, `step_results`, `exchanges` (masked, size-capped bodies as `jsonb`),
  `events`, `ai_calls` (feature, provider, model, tokens, cost, prompt hash, output, user), `rule_reviews`,
  `clauses` (text, `tsvector`, `vector(1024)`).
- `run.json` + reports stay as they are; the store is an index of them. `aqt db import` loads existing history.
- Masking before write: `Authorization`, `code`, tokens, `PSU-ID`, configurable JSON paths (IBANs, names),
  reusing the engine's report masking.
- Retention: exchanges 90 days (configurable in `monitoring.yaml`), results and summaries kept — built in Phase 6 (`aqt db prune`, daily in the monitor).
- Ask-the-agent tools are **read-only** and run through a DB role with SELECT only on views.

## 7. Phases (hackathon order)

| Phase | Deliverable | Demo |
|---|---|---|
| 0. Foundations ✅ | .NET SDK on the Mac; baseline commit; `docker-compose` Postgres; `ApiTestTool.Store` + migrations + import; runs written to the store; `ApiTestTool.AI` skeleton (config, providers, masking, audit) | Runs from CLI/portal appear in Postgres |
| 1. Run summaries ✅ | Summary panel on the Run page, `aqt ai summarize` | Run TBC-MOCK → one-click triage of failures, Claude vs local |
| 2. Standards + rules ✅ | Ingest BG 1.3.16 + Georgian guides; clause search; rule drafting; review queue | Pick a clause → AI drafts rules with quotes → approve in the portal |
| 3. Scenario drafting ✅ | Draft → validate → MockBank loop; drafts page; coverage matrix (`aqt scenarios`, `aqt coverage`) | Approved rule → generated scenario → green on MockBank → coverage goes up |
| 4. Ask-the-agent ✅ | Ask page + `aqt ask` | "Why did TBC fail last night?" → answer citing run IDs and clauses |
| 5. PIS ✅ | §9 | Payment consent → SCA → status, on MockBank |
| 6. Monitoring ✅ | Scheduled runs (`monitoring.yaml`, portal monitor, `aqt monitor run-due`), regression alerts (+ webhook), trends, retention | Nightly MockBank run; a regression shows on Alerts and in the webhook |

Each AI feature gets a small eval set (inputs + expected outcome) before staff use it.

## 8. Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Data policy / provider | Hybrid: Claude for drafting (public documents only); summaries and the agent can switch to local Ollama; masking always on |
| 2 | Documents | Latest versions, English. BG NextGenPSD2 IG **1.3.16** (2025-12-02) + OpenAPI 1.3 (gitlab.com/the-berlin-group/nextgenpsd2). Georgian: openfinance.ge/en/standards — XS2A IG 0.8.RC3, JWS 0.5.RC1, IAA 0.8.RC1, Identity Assurance 0.3.RC3; NBG Rule 80/04 (English) |
| 2a | Georgian docs language | Confirmed Georgian-only on openfinance.ge. The Georgian text is the source of truth (quotes verified against it); an English translation is made **per rule** at drafting time (marked as AI translation) instead of translating whole documents up front |
| 2b | Rules storage | **Database is the source of truth** (status workflow, history, search); approved rules are exported to `rules/<prefix>/<ID>.yaml` for git (`aqt rules export`). Changed from "files first" because the review queue, history and duplicate search need the database |
| 3 | Database | PostgreSQL (Docker for dev) |
| 4 | Approvers | The team now (reviewer name), staff with a role later |
| 5 | Scope | AIS first, PIS designed in now, built in Phase 5 |
| 6 | MockBank | Grows with the rules: when a rule is approved and MockBank doesn't enforce it, that is a task, so generated scenarios can be proven |
| 7 | Scenario drafts | Code owns id, suite, severity, `specRef`, `origin`, `rules:`; the model writes title, description, steps. At most 3 attempts with engine errors / dry-run failures fed back. A failed dry run the model calls a MockBank gap (`MOCK_GAP`) is accepted only for a failed expectation (not a tool error) and is shown to the reviewer, who decides. Drafts live in `scenario_drafts`; approval writes the file and links the rule (`rule_reviews` action `linked`) |
| 9 | Monitoring | Schedules live in `monitoring.yaml` (versioned like scenarios), their last run in `monitor_state`. Unattended runs skip SCA scenarios except on MockBank (no one to log in; `--sca-auto` stays MockBank-only). Alerts are computed in code against the bank's previous run, deduplicated per run/kind/scenario; bank_down is not repeated while a bank stays down. Notification = optional webhook, masked |
| 8 | Ask-agent safety | Read-only tools in code plus one SQL tool. The SQL tool logs in as `aqt_agent` (own login, member of nothing): SELECT on views in schema `agent` only, no headers/bodies/AI audit, one prepared statement, read-only, 5 s timeout. `SET ROLE` from the main user was tried first and rejected: the SQL could switch back with `set_config('role', …)` or a second statement. Ids in answers are checked against tool results in code |

## 9. PIS — built in Phase 5 (2026-10-25)

Built as planned, following GE-XS2A 0.8.RC3 chapter 8: MockBank payments (`/v1/payments/{aspsp|domestic|foreign}`,
status, details, cancellation, authorisations; lifecycle ACTC → ACSP → ACSC or ACCC for intra-bank; table-4 field checks;
TPP-Rejection-NoFunds-Preferred), the `poll:` request option and `expect.jsonOneOf`, templates `payment.*`, the `pis`
suite (PIS-001…005) and NEG-PIS-001…007. Gate: `requires: variables.pis.*` (sandbox test accounts in the profile),
enforced in code for AI drafts. Not built: bulk and periodic payments, signing baskets (MockBank answers 404).

The original design notes:

What we do now so PIS needs no redesign:
- `service: AIS | PIS | COMMON` on rules, scenarios (via suite) and store rows; coverage matrix split per service.
- Rule areas reserved: `PIS` (initiation), `PST` (payment status), `PCN` (cancellation), `SBK` (signing baskets, if the NBG guide requires them).
- Scenario DSL already supports what PIS needs (POST + `_links` + SCA + status polling). One addition is
  likely: a `poll:` option on a request step (repeat until `transactionStatus` is final or a timeout).

What Phase 5 builds:
- Templates `payment.*` (payment products from the Georgian guide, e.g. domestic GEL and SEPA-like
  instant/cross-border as listed there), `pis` suite: initiate → start authorisation → OAuth SCA with
  `PIS:<paymentId>` scope → status → cancellation where allowed; negative cases (wrong amount format,
  unknown product, missing debtor account, unsupported currency).
- MockBank: payment endpoints, payment status lifecycle (`RCVD` → `ACTC` → `ACSC`/`RJCT`), SCA binding to payment.
- Safety: PIS runs against real sandboxes only with test accounts from the profile; never with `--sca-auto`.
