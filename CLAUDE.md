# CLAUDE.md

ApiTestTool (`aqt`) tests banks' XS2A (Berlin Group NextGenPSD2 / NBG Georgia) sandboxes as a TPP:
mTLS with a QWAC, requests signed with a QSealC (`Digest` + `X-Jws-Signature`), OAuth/PKCE SCA, and
every response checked against the standard. .NET 10, C#, data-driven by YAML. Hackathon project.

- `README.md` — user manual · `docs/ai/PLAN.md` — AI layer plan and decisions
- `ARCHITECTURE.md` — Mermaid snapshot of the current system; **update it at the end of every phase**
- `NOTE.md` — work journal; **append an entry for every step**: what was done and why (newest at the bottom)

## Commands

```bash
dotnet build ApiTestTool.sln
dotnet test                                                          # engine unit tests (xUnit)
dotnet run --project src/ApiTestTool.Cli -- validate                 # load and check every YAML file
dotnet run --project src/ApiTestTool.Cli -- run --bank MOCK --with-mock --sca-auto   # full run, no real bank
dotnet run --project src/ApiTestTool.Portal --launch-profile Portal  # web UI on http://localhost:5280/
docker compose up -d                                                 # PostgreSQL on localhost:5435
dotnet run --project src/ApiTestTool.Cli -- db migrate|import|status # store
dotnet run --project src/ApiTestTool.Cli -- ai check                 # Claude + Ollama reachable, audited
dotnet run --project src/ApiTestTool.Cli -- ai summarize latest --provider ollama --show-input   # AI run triage
dotnet run --project src/ApiTestTool.Cli -- standards fetch && dotnet run --project src/ApiTestTool.Cli -- standards ingest
dotnet run --project src/ApiTestTool.Cli -- rules draft <clause-key> --provider ollama          # AI rule drafts
AQT_TEST_DB="Host=localhost;Port=5435;Username=aqt;Password=aqt_dev" dotnet test   # include database tests
dotnet tool restore && dotnet ef migrations add <Name> --project src/ApiTestTool.Store --output-dir Migrations
```

After changing YAML, run `validate`. After changing engine code, run `dotnet test` and a MOCK run.
After changing store entities, add a migration. Run `dotnet` from the repo root (the CLI finds `banks/` there).

## Layout

| Path | Role |
|---|---|
| `src/ApiTestTool.Engine` | Core library: YAML loading, scenario runner, HTTP/TLS/SCA steps, JWS signing, OAuth, reports. No UI, no AI |
| `src/ApiTestTool.Cli` | `aqt` executable (`run`, `list`, `validate`, `mock`) |
| `src/ApiTestTool.MockBank` | In-memory fake ASPSP enforcing real XS2A checks; the reference target for new scenarios |
| `src/ApiTestTool.Portal` | Blazor Server UI; `Services/RunService.cs` queues runs, `StoreRecorder.cs` writes them to PostgreSQL |
| `src/ApiTestTool.Store` | EF Core + Npgsql store (`AqtStore`), `SensitiveDataMasker`, `RunImporter`, migrations |
| `src/ApiTestTool.Standards` | No AI/DB: `PdfClauseReader` (TOC-driven), `DocxClauseReader` (heading styles), `QuoteVerifier`, `StandardSources` (manifest, fetch, SHA-256) |
| `src/ApiTestTool.AI` | `ChatClientFactory` (Claude / Ollama per feature, embeddings), `AuditingChatClient` (mask + audit every call), `Summaries/` (run triage), `Standards/StandardsIngestor`, `Rules/` (`RuleDrafter`, `RuleBook` review + export, `NormativeLevel`) |
| `tests/ApiTestTool.*.Tests` | xUnit: engine (every shipped YAML end to end), store (masking, import, DB with `AQT_TEST_DB`), AI |
| `banks/<BIC>.yaml` | Bank profiles; `extends: _shared.yaml`; `_`-prefixed files are not run |
| `scenarios/<suite>/*.yaml` | Test cases; reusable headers/bodies in `scenarios/_templates` |
| `rules/` | Approved rules exported from the database (`aqt rules export`); the database is the source of truth |
| `standards/` | `sources.yaml` manifest (URL, version, SHA-256); downloaded files under `sources/` are git-ignored |
| `docs/ai/` | AI feature plan and design notes |

## Hard constraints

- **Standalone:** only public nuget.org packages, versions in `Directory.Packages.props` (central
  package management — no `Version=` on `PackageReference`). Never reference B7.PSD2/AltaSoft projects.
- **Never commit secrets:** `certs/` contents, `*.pfx`, API keys. Keys come from environment variables
  (`AQT_QWAC_PASSWORD`, `AQT_QSEALC_PASSWORD`, `ANTHROPIC_API_KEY`).
- **`--sca-auto` is for MockBank / public mocks only** — never add it to a real bank's flow.
- Codes, tokens and other secrets are masked in reports; any new output or store must mask them too.
- Every scenario needs a `specRef` naming the clause it tests.

## AI layer rules (see docs/ai/PLAN.md)

- `ApiTestTool.Engine` stays deterministic and AI-free. AI code lives in `src/ApiTestTool.AI` and only
  *produces artifacts* (draft rules, draft scenarios, summaries) or *answers questions*; the engine
  executes. An AI never sends requests to a bank directly.
- AI output is a **draft** until a human approves it (`status: draft` → `approved` happens only in the
  portal review screen, recording reviewer and time). Draft scenarios never run against real banks.
- Every rule and generated scenario must cite a clause, and its verbatim `quote` must be found in the
  indexed standard text (`QuoteVerifier`, checked in code, not by the model). Georgian documents stay Georgian;
  quotes are verified against the Georgian text, English translations are per rule and marked as AI-made.
- Only `RuleBook` changes a rule's status, always with a reviewer name and a `rule_reviews` row.
- Every model call is written to the audit log (model, prompt hash, token usage, output, who triggered it).
- Data sent to the model is masked first (tokens, auth codes, PSU identifiers, IBANs as configured).
- Providers: rule and scenario drafting use the official `Anthropic` SDK (`claude-opus-5-5`). Run summaries
  and the ask-agent go through `Microsoft.Extensions.AI` `IChatClient`, Claude or Ollama chosen by config.
  Embeddings come from local Ollama `bge-m3`. Models and providers come from config, never hard-coded.
- Store: PostgreSQL (`docker compose up -d`, port 5435; connection `ConnectionStrings:Aqt` / env `AQT_DB`).
  CLI and portal runs must still work without a database — a store failure is a notice, never a failed run.
- Everything written to the store or sent to a model goes through `SensitiveDataMasker` first.
- Facts go in code, judgement goes to the model: counts, run-to-run changes, clause existence and scenario ids are
  computed or checked in code (see `RunDigest`, `RunSummary.Validate`); the model explains and groups.
- Claude requests go through the beta endpoint with `fallbacks: "default"` (refusal fallback) and per-feature
  effort; `AnthropicRequestTests` captures the real request — keep it passing when touching the provider code.
- AIS first; PIS is designed in (`service: AIS | PIS | COMMON`) — don't add anything AIS-only to shared code.

## Style

Match the existing code: file-scoped namespaces, `sealed` classes, `required`/`init` properties, XML
`<summary>` comments on public types, nullable enabled. Keep README tables in sync when adding commands,
profile fields, scenario fields or suites.
