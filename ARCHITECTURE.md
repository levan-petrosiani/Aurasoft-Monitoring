# Architecture

Snapshot of the system as it is **now**. Update this file at the end of every phase (see `docs/ai/PLAN.md`).
Diagrams are Mermaid (render in GitHub, GitLab, Rider, VS Code).

**Last updated:** 2026-10-24, after porting the Store, Standards and AI layers (Phases 0–2) to Python. Everything is
Python now: engine, CLI, MockBank, portal, and the optional store/AI layer (`aqt/store`, `aqt/standards`, `aqt/ai`,
`aqt/cli_ai.py`, `aqt/portal/ai_services.py`). The C# implementation is in git history (last C# commit `8abf9f6`).

## 1. Components

```mermaid
flowchart LR
    subgraph Inputs["Data on disk (YAML)"]
        Banks["banks/*.yaml<br/>bank profiles"]
        Scen["scenarios/**/*.yaml<br/>test cases"]
        Certs["certs/<br/>QWAC + QSealC (PFX)"]
    end

    subgraph Hosts["Entry points"]
        Cli["aqt/cli.py (aqt)<br/>run · mock · portal · list · validate<br/>db · ai · standards · rules (cli_ai.py)"]
        Portal["aqt/portal (Flask :5280)<br/>RunService → reports/portal-python<br/>AI summary · Standards · Rules"]
    end

    subgraph AiLayer["Optional store + AI layer (pip install -e .[ai])"]
        Store["aqt/store<br/>PostgreSQL · masking · import · search"]
        Std["aqt/standards<br/>PDF/Word clause readers · quote verifier"]
        AI["aqt/ai<br/>providers + audit · summaries · ingest<br/>rule drafter · RuleBook"]
    end
    PG[("PostgreSQL 17 + pgvector<br/>docker :5435")]
    Claude["Claude API<br/>claude-opus-5-5"]
    Ollama["Ollama (local)<br/>qwen3:8b · bge-m3"]

    Engine["aqt (engine)<br/>loader · runner · steps · signing · OAuth · reports<br/>(no AI, no DB)"]
    Mock["aqt/mockbank<br/>fake ASPSP :7443 (mTLS) / :7080"]
    Reports[("reports/&lt;BIC&gt;-&lt;time&gt;/<br/>report.html · summary.json · junit.xml")]

    subgraph Targets["Banks under test"]
        Real["Bank sandboxes"]
        TbcMock["TBC public mock"]
    end

    Banks --> Engine
    Scen --> Engine
    Certs --> Engine
    Cli --> Engine
    Portal --> Engine
    Cli -. "--with-mock" .-> Mock
    Portal -. "Start MockBank" .-> Mock
    Engine -- "mTLS + JWS" --> Mock
    Engine -- "mTLS + JWS" --> Real
    Engine --> TbcMock
    Engine --> Reports

    Cli -- "run --db / AQT_DB" --> Store
    Portal -- "background writes" --> Store
    Cli --> AI
    Portal --> AI
    Std --> AI
    AI --> Store
    Store -- "masked rows" --> PG
    AI -- "masked prompts" --> Claude
    AI -- "masked prompts" --> Ollama
```

The engine has no AI and no database dependency. Everything that reaches PostgreSQL or a model passes
`aqt/store/masking.py` first; a store failure never fails a run (the files on disk are complete and can be imported).

## 2. Engine internals

```mermaid
flowchart TB
    Y["yamljson<br/>YAML → JSON values<br/>(plain scalars typed, quoted = string)"] --> B["binding<br/>→ dataclasses, camelCase,<br/>unknown properties rejected"]
    B --> P["profiles<br/>extends: _shared.yaml (deep merge)"]
    B --> S["scenarios<br/>+ TemplateLibrary (_templates)"]
    P --> C["context.RunContext<br/>certificates · JwsSigner · HttpClient (mTLS)"]
    S --> R["runner.ScenarioRunner<br/>preflight first · gate · requires · cleanup"]
    C --> R
    R --> St["steps<br/>request · tls · sca · localCertificates"]
    St --> V["variables + jsonpath<br/>{{...}}, save, expectations"]
    St --> Sig["signing<br/>X-Jws-Signature, Digest, verify responses"]
    St --> O["oauth<br/>discovery · PKCE · callback listener · headless approval"]
    R --> Rep["reporting<br/>html · json · junit"]
```

| Module | Notes |
|---|---|
| `certificates` | PFX via `cryptography`; self-signed QWAC/QSealC with organizationIdentifier (OID 2.5.4.97); names printed like .NET (`CN=…, O=…, OID.2.5.4.97=…, C=GE`) |
| `signing` | Detached JWS, `b64=false`, `sigD.pars` = x-request-id + PSU/client headers + digest; PS256 (RSA-PSS, salt = hash length) or ES256 (r‖s) |
| `httpclient` | `http.client` + `ssl`: client certificate, no redirects, no decompression, keep-alive per host, optional retries (`limits.retry`) |
| `oauth` | Callback listener on 127.0.0.1 and ::1; headless approval only on pages without a password field |
| `mockbank` | `http.server` threads; HTTPS via pyOpenSSL so any client certificate is accepted and checked by the XS2A middleware |

## 3. One AIS scenario at runtime (AIS-001)

```mermaid
sequenceDiagram
    participant R as Runner
    participant B as Bank (XS2A, mTLS)
    participant A as Auth server
    participant L as Callback listener :5199
    R->>B: POST v2/consents/account-access (signed)
    B-->>R: 201 consentId, _links.startAuthorisation (signed response verified)
    R->>B: POST start authorisation
    B-->>R: 201 _links.scaOAuth
    R->>A: GET discovery
    R->>A: browser / headless: authorize (PKCE S256, state)
    A-->>L: redirect ?code&state
    R->>A: POST token (mTLS, code_verifier) → access + refresh token
    R->>A: POST token (refresh_token)
    R->>B: GET consent status / accounts / balances (Bearer + Consent-ID)
    R->>B: DELETE consent (cleanup)
```

## 4. AI run summary

```mermaid
flowchart LR
    Run[("stored run<br/>masked exchanges")] --> Digest["build_digest (code)<br/>problems + evidence<br/>changes vs previous run"]
    Prev[("bank's previous run")] --> Digest
    Digest --> Model{{"Claude or Ollama<br/>JSON triage<br/>(audited in ai_calls)"}}
    Model --> Validate["RunSummary.validate (code)<br/>unknown scenarios dropped · clauses must be in the run<br/>passed → warnings · skipped → dropped<br/>comparison written in code"]
    Validate --> Saved[("run_summaries")]
```

## 5. Standards → rules → human review

```mermaid
flowchart LR
    Src[("standards/sources.yaml<br/>URL + SHA-256")] -->|fetch + verify| Files[("PDF / .docx")]
    Files --> Read["readers (code)<br/>PDF: TOC → headings<br/>Word: heading styles"]
    Read --> Clauses[("clauses<br/>tsvector + vector(1024)")]
    Clauses --> Draft{{"RuleDrafter<br/>Claude or local model"}}
    Draft --> Check["checks (code)<br/>quote in clause → else invalid<br/>level vs wording · duplicates"]
    Check --> Queue[("rules: draft / invalid")]
    Queue --> Review["person (portal / CLI)<br/>approve · edit · reject · reopen"]
    Review --> Approved[("rules: approved<br/>rule_reviews history")]
    Approved -->|aqt rules export| Yaml[("rules/*.yaml")]
```

## 6. Store schema

```mermaid
erDiagram
    runs ||--o{ scenario_results : "cascade"
    scenario_results ||--o{ step_results : "cascade"
    step_results ||--o{ exchanges : "cascade"
    runs ||..o{ events : "run_id"
    runs ||..o{ run_summaries : "run_id"
    ai_calls ||..o{ run_summaries : "ai_call_id"
    standard_documents ||--o{ clauses : "id + version"
    clauses ||..o{ rules : "clause_key"
    rules ||--o{ rule_reviews : "history"
```

Migrations are SQL files in `aqt/store/migrations/`, tracked in `aqt_schema_migrations`. `0001` is the schema the C#
tool created; a database created by the C# tool is recognised (its EF Core history) and kept as it is.

## 7. Tech facts

- Python 3.11+; PyYAML, cryptography, pyOpenSSL, Flask; pytest. Optional `ai` extra: psycopg 3 + pgvector, anthropic,
  pdfplumber (Ollama is called over HTTP).
- Ports: portal 5280, MockBank 7443 (HTTPS, mTLS) and 7080 (HTTP pages), OAuth callback 5199, PostgreSQL 5435,
  Ollama 11434.
- Tests: 93 with `AQT_TEST_DB` (22 engine/portal, 71 store/standards/AI/portal-AI); without a database 76 pass and the
  17 database tests are skipped.
- Reports keep the C# `summary.json` shape (camelCase, nulls omitted); interop with the C# client and MockBank was
  verified in both directions before the C# code was removed.
