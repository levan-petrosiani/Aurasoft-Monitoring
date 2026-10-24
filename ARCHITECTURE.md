# Architecture

Snapshot of the system as it is **now**. Update this file at the end of every phase (see `docs/ai/PLAN.md`);
the target design lives in the plan. Diagrams are Mermaid (render in GitHub, GitLab, Rider, VS Code).

**Last updated:** 2026-10-24, end of **Phase 2** (standards → AI-drafted rules → human review).
Phase 0: PostgreSQL store, masking, run import, AI provider factory with an audit log. Phase 1: AI run summaries.
Phase 2: `ApiTestTool.Standards` (clause readers, quote verifier, sources manifest), clauses with full-text and vector
search, rule drafting with checks in code, the review workflow (`RuleBook`) and the portal's Standards and Rules pages.

## 1. Components

```mermaid
flowchart LR
    subgraph Inputs["Data on disk (YAML)"]
        Banks["banks/*.yaml<br/>bank profiles"]
        Scen["scenarios/**/*.yaml<br/>test cases"]
        Certs["certs/<br/>QWAC + QSealC"]
    end

    subgraph Hosts["Entry points"]
        Cli["ApiTestTool.Cli (aqt)<br/>run · db · ai · standards · rules"]
        Portal["ApiTestTool.Portal<br/>Blazor :5280<br/>RunService → StoreRecorder<br/>AI summary · Standards · Rules"]
    end

    Engine["ApiTestTool.Engine<br/>loader · runner · signing · OAuth · reports<br/>(no AI, no DB)"]
    Mock["ApiTestTool.MockBank<br/>fake ASPSP :7443 / :7080"]

    subgraph New["Phases 0–2"]
        Store["ApiTestTool.Store<br/>AqtStore · RunImporter<br/>SensitiveDataMasker · EF migrations<br/>clause search (tsvector + pgvector)"]
        AI["ApiTestTool.AI<br/>ChatClientFactory · AuditingChatClient<br/>Summaries · StandardsIngestor<br/>RuleDrafter · RuleBook"]
        Std["ApiTestTool.Standards<br/>PdfClauseReader · DocxClauseReader<br/>QuoteVerifier · StandardSources"]
    end
    Docs[("standards/sources.yaml<br/>BG-IG 1.3.16 PDF<br/>GE-XS2A/JWS/IAA .docx")]
    RulesDir[("rules/*.yaml<br/>approved, exported")]

    subgraph Targets["Banks under test"]
        Real["Bank sandboxes"]
        TbcMock["TBC public mock"]
    end

    Files[("reports/ · data/runs/<br/>run.json · summary.json<br/>report.html · junit.xml")]
    PG[("PostgreSQL 17 + pgvector<br/>docker :5435")]
    Claude["Claude API (beta endpoint)<br/>claude-opus-5-5 · effort per feature<br/>fallbacks: default"]
    Ollama["Ollama (local)<br/>qwen3:8b (default, num_ctx 16k)<br/>gemma4:26b · bge-m3 embeddings"]

    Banks & Scen & Certs --> Engine
    Cli --> Engine
    Portal --> Engine
    Cli -. "--with-mock" .-> Mock
    Portal -. "start/stop" .-> Mock
    Engine -- "mTLS + JWS + OAuth/PKCE" --> Mock & Real
    Engine -- HTTPS --> TbcMock
    Engine --> Files

    Cli -- "run --db / AQT_DB" --> Store
    Cli -- "db import" --> Files
    Portal -- "background writes" --> Store
    Store -- "masked rows" --> PG
    Cli -- "ai check / summarize" --> AI
    Portal -- "SummaryService" --> AI
    AI -- "read runs, save summaries" --> Store
    Docs --> Std
    Std --> AI
    AI -- "clauses, rules, reviews" --> Store
    AI -- "export approved" --> RulesDir
    Portal -- "RuleService" --> AI
    AI -- "masked prompts" --> Claude
    AI -- "masked prompts" --> Ollama
    AI -- "audit (ai_calls)" --> Store
```

Rules the diagram encodes: the engine has no AI and no database dependency; everything that reaches PostgreSQL
or a model passes `SensitiveDataMasker`; a store failure never fails a run (the files on disk are complete and
can be imported later).

## 2. AI run summary (Phase 1)

```mermaid
flowchart LR
    Run[("runs + scenario/step results<br/>+ masked exchanges")] --> Digest
    Prev[("bank's previous run")] --> Digest
    Digest["RunDigest (code)<br/>problems + evidence excerpts<br/>outcome changes · pass rates<br/>scenario ids · clause refs"] --> Prompt
    Prompt["System prompt<br/>outcome semantics · side rules<br/>JSON schema"] --> Model{{"Claude or Ollama<br/>(AuditingChatClient → ai_calls)"}}
    Model --> Parse["Parse JSON<br/>(1 repair retry)"]
    Parse --> Validate["Validate (code)<br/>drop unknown scenarios<br/>clauses must be in the run<br/>passed-scenario findings → warnings<br/>flag unexplained failures<br/>comparison text from code"]
    Validate --> Saved[("run_summaries")]
    Saved --> UI["Portal panel · aqt ai summarize"]
```

The model decides grouping, side (bank / our side / environment / unclear), explanation and next step. Everything
that can be checked or computed — counts, run-to-run changes, scenario ids, clause references — is done in code.

## 3. Standards → rules (Phase 2)

```mermaid
flowchart LR
    Src[("sources.yaml<br/>URL + SHA-256")] -->|fetch, verify hash| Files[("PDF / .docx")]
    Files --> Read["Clause readers (code)<br/>PDF: TOC → headings, deeper levels<br/>Word: heading styles → numbers"]
    Read --> Clauses[("clauses<br/>tsvector · vector(1024)")]
    Clauses -->|bge-m3, local| Clauses
    Clauses --> Draft{{"RuleDrafter<br/>Claude (default) or local model<br/>prompt: normative statements → rules"}}
    Draft --> Check["Checks (code)<br/>quote ∈ clause text → else invalid<br/>level backed by keywords<br/>duplicate = close meaning AND same identifiers<br/>ids BG/GE-AREA-NNN"]
    Check --> Queue[("rules: draft / invalid<br/>rule_reviews: drafted")]
    Queue --> Review["Person reviews<br/>(portal Rules / Standards, CLI)<br/>approve · edit (quote re-checked) · reject (reason) · reopen"]
    Review --> Approved[("rules: approved<br/>rule_reviews: who, when, before/after")]
    Approved -->|aqt rules export| Yaml[("rules/*.yaml in git")]
```

## 4. Engine internals (unchanged since Phase 0)

```mermaid
flowchart TB
    subgraph Load["Load"]
        BPL["BankProfileLoader<br/>extends: _shared.yaml"]
        SL["ScenarioLoader + TemplateLibrary"]
        CL["CertificateLoader / DevCertificates"]
    end

    subgraph Run["ScenarioRunner"]
        Gate{"Preflight<br/>Critical failed?"}
        Exec["Step executors"]
        HTTP["HttpStepExecutor<br/>+ Xs2aHttpClient + RetryHandler"]
        TLS["TlsStepExecutor"]
        LC["LocalCertificatesStepExecutor"]
        SCA["ScaStepExecutor<br/>OAuthClient · Pkce · callback :5199<br/>HeadlessAuthorizer (mock only)"]
        Eval["ExpectationEvaluator<br/>JsonPathLite · VariableResolver"]
    end

    Sign["Signing<br/>Digest · JwsSigner · JwsVerifier"]
    RW["ReportWriter<br/>html · json · junit"]

    BPL --> Run
    SL --> Run
    CL --> Sign
    Gate -- "no" --> Exec
    Gate -- "yes" --> Blocked["remaining scenarios = Blocked"]
    Exec --> HTTP & TLS & LC & SCA
    HTTP --> Sign
    HTTP --> Eval
    Run --> RW
```

## 5. Store schema

```mermaid
erDiagram
    runs ||--o{ scenario_results : "cascade"
    scenario_results ||--o{ step_results : "cascade"
    step_results ||--o{ exchanges : "cascade"
    runs ||..o{ events : "run_id (kept after delete)"
    runs ||..o{ ai_calls : "run_id (kept after delete)"
    runs ||..o{ run_summaries : "run_id (kept after delete)"
    run_summaries }o..|| ai_calls : "ai_call_id"
    standard_documents ||--o{ clauses : "standard + version"
    clauses ||..o{ rules : "clause_key"
    rules ||--o{ rule_reviews : "rule_id"
    rules }o..|| ai_calls : "ai_call_id"

    runs {
        text id PK "run folder name"
        text source "cli | portal"
        text status "Queued..Completed/Failed/Cancelled"
        text bank_bic
        timestamptz started_utc
        int passed_failed_errors_blocked_skipped
        float pass_rate
        text_array suites
        jsonb options_json
        text report_directory
    }
    scenario_results {
        bigint id PK
        text run_id FK
        text scenario_id
        text suite
        text severity
        text spec_ref
        text outcome
        text reason
    }
    step_results {
        bigint id PK
        text run_id
        bool is_cleanup
        text kind "request | tls | sca | localCertificates"
        text outcome
        text_array failures_warnings_info
    }
    exchanges {
        bigint id PK
        text run_id
        text method
        text url "masked query"
        int status_code
        jsonb request_headers "masked"
        text request_body "masked, capped 64KB"
        jsonb response_headers
        text response_body "masked, capped"
        bool truncated
    }
    events {
        bigint id PK
        timestamptz time_utc
        text run_id
        text source "cli | portal | import"
        text type "run.started, scenario.completed, ..."
        text level
        text message
        jsonb data
    }
    run_summaries {
        uuid id PK
        text run_id
        text provider
        text model
        text status "ok | unstructured | failed"
        jsonb summary_json "validated summary"
        text text "raw answer or error"
        text_array validation_notes
        uuid ai_call_id
    }
    standard_documents {
        text id PK "BG-IG, GE-XS2A"
        text version PK
        text language "en | ka"
        text sha256
        int clause_count
    }
    clauses {
        bigint id PK
        text key UK "BG-IG:1.3.16:6.3.1.2"
        text number
        text title
        text text
        int level
        tsvector search "generated; english or simple"
        vector embedding "bge-m3, 1024"
    }
    rules {
        text id PK "GE-CON-001"
        text status "draft | approved | rejected | retired | invalid"
        text level "MUST | SHOULD | MAY"
        text service "AIS | PIS | COMMON"
        text requirement
        text quote "verbatim, checked"
        text quote_english "AI translation"
        jsonb check_json
        text_array check_notes
        text similar_to
        text reviewed_by
        vector embedding
    }
    rule_reviews {
        bigint id PK
        text rule_id
        text reviewer
        text action "drafted | edited | approved | rejected | reopened"
        jsonb before
        jsonb after
    }
    ai_calls {
        uuid id PK
        text feature "summary | ask | ruleDrafting | scenarioDrafting | check"
        text provider "anthropic | ollama"
        text model
        text prompt_sha256
        jsonb request "masked messages"
        text response
        bigint input_tokens
        bigint output_tokens
        bool succeeded
    }
```

## 6. One AIS scenario at runtime (AIS-001)

```mermaid
sequenceDiagram
    autonumber
    participant R as ScenarioRunner
    participant B as Bank XS2A
    participant A as Bank Auth server
    participant U as Tester (browser)
    participant S as Store (after the run)
    R->>B: POST v2/consents/account-access (mTLS, Digest, X-Jws-Signature)
    B-->>R: 201 consentId, _links.startAuthorisation
    R->>B: POST startAuthorisation
    B-->>R: 201 _links.scaOAuth
    R->>A: GET .well-known (discovery)
    R->>U: open authorize URL (PKCE S256, state)
    U->>A: login + approve
    A-->>R: redirect localhost:5199/callback?code&state
    R->>A: POST token (mTLS) → access + refresh token
    R->>B: GET consent status → valid
    R->>B: GET accounts / balances (Consent-ID + Bearer)
    R->>B: DELETE consent (cleanup)
    R->>S: results + exchanges (tokens, codes, IBANs masked)
```

## 7. Tech facts

| Item | Value |
|---|---|
| Runtime | .NET 10 (SDK 10.0.402), C# latest, nullable on |
| Packages | YamlDotNet · Npgsql.EntityFrameworkCore.PostgreSQL 10.0.3 + EFCore.NamingConventions · Pgvector.EntityFrameworkCore 0.3 · PdfPig 0.1.16 · Anthropic 13.3.0 · Microsoft.Extensions.AI 10.11 · OllamaSharp 5.5 · xUnit |
| Storage | YAML in; `run.json` + reports out; PostgreSQL 17 (pgvector image) as the queryable store |
| Ports | Portal 5280 · MockBank 7443 / 7080 · OAuth callback 5199 · PostgreSQL 5435 · Ollama 11434 |
| Tools | `dotnet-ef` 10.0.13 as a local tool (`dotnet tool restore`) |
| Tests | 122: engine 53 · standards 17 · store 19 (4 need `AQT_TEST_DB`) · AI 33 (8 need `AQT_TEST_DB`) |
| Standards in the store | BG-IG 1.3.16 (182 clauses) · GE-XS2A 0.8.RC3 (133) · GE-JWS 0.5.RC1 (32) · GE-IAA 0.8.RC1 (32), all embedded |
| Services covered | AIS (consents, accounts, balances, transactions), preflight, negative. PIS in Phase 5 |
