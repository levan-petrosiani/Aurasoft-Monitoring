# Architecture

Snapshot of the system as it is **now**. Update this file at the end of every phase (see `docs/ai/PLAN.md`);
the target design lives in the plan. Diagrams are Mermaid (render in GitHub, GitLab, Rider, VS Code).

**Last updated:** 2026-10-24, end of **Phase 0** (store + AI foundations). New in this phase: PostgreSQL store,
masking, run import, AI provider factory with an audit log. No AI features yet.

## 1. Components

```mermaid
flowchart LR
    subgraph Inputs["Data on disk (YAML)"]
        Banks["banks/*.yaml<br/>bank profiles"]
        Scen["scenarios/**/*.yaml<br/>test cases"]
        Certs["certs/<br/>QWAC + QSealC"]
    end

    subgraph Hosts["Entry points"]
        Cli["ApiTestTool.Cli (aqt)<br/>run · db · ai check"]
        Portal["ApiTestTool.Portal<br/>Blazor :5280<br/>RunService → StoreRecorder"]
    end

    Engine["ApiTestTool.Engine<br/>loader · runner · signing · OAuth · reports<br/>(no AI, no DB)"]
    Mock["ApiTestTool.MockBank<br/>fake ASPSP :7443 / :7080"]

    subgraph New["New in Phase 0"]
        Store["ApiTestTool.Store<br/>AqtStore · RunImporter<br/>SensitiveDataMasker · EF migrations"]
        AI["ApiTestTool.AI<br/>ChatClientFactory<br/>AuditingChatClient"]
    end

    subgraph Targets["Banks under test"]
        Real["Bank sandboxes"]
        TbcMock["TBC public mock"]
    end

    Files[("reports/ · data/runs/<br/>run.json · summary.json<br/>report.html · junit.xml")]
    PG[("PostgreSQL 17 + pgvector<br/>docker :5435")]
    Claude["Claude API<br/>claude-opus-5-5"]
    Ollama["Ollama (local)<br/>gemma4:26b · qwen3:8b · bge-m3"]

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
    Cli -- "ai check" --> AI
    AI -- "masked prompts" --> Claude
    AI -- "masked prompts" --> Ollama
    AI -- "audit (ai_calls)" --> Store
```

Rules the diagram encodes: the engine has no AI and no database dependency; everything that reaches PostgreSQL
or a model passes `SensitiveDataMasker`; a store failure never fails a run (the files on disk are complete and
can be imported later).

## 2. Engine internals (unchanged in Phase 0)

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

## 3. Store schema

```mermaid
erDiagram
    runs ||--o{ scenario_results : "cascade"
    scenario_results ||--o{ step_results : "cascade"
    step_results ||--o{ exchanges : "cascade"
    runs ||..o{ events : "run_id (kept after delete)"
    runs ||..o{ ai_calls : "run_id (kept after delete)"

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

## 4. One AIS scenario at runtime (AIS-001)

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

## 5. Tech facts

| Item | Value |
|---|---|
| Runtime | .NET 10 (SDK 10.0.402), C# latest, nullable on |
| Packages | YamlDotNet · Npgsql.EntityFrameworkCore.PostgreSQL 10.0.3 + EFCore.NamingConventions · Anthropic 13.3.0 · Microsoft.Extensions.AI 10.11 · OllamaSharp 5.5 · xUnit |
| Storage | YAML in; `run.json` + reports out; PostgreSQL 17 (pgvector image) as the queryable store |
| Ports | Portal 5280 · MockBank 7443 / 7080 · OAuth callback 5199 · PostgreSQL 5435 · Ollama 11434 |
| Tools | `dotnet-ef` 10.0.13 as a local tool (`dotnet tool restore`) |
| Tests | 75: engine 53 · store 17 (3 need `AQT_TEST_DB`) · AI 5 |
| Services covered | AIS (consents, accounts, balances, transactions), preflight, negative. PIS in Phase 5 |
