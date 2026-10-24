# Architecture

Snapshot of the system as it is **now**. Update this file at the end of every phase (see `docs/ai/PLAN.md`);
the target design lives in the plan. Diagrams are Mermaid (render in GitHub, GitLab, Rider, VS Code).

**Last updated:** 2026-10-24 — before Phase 0 (no AI layer yet).

## 1. Components

```mermaid
flowchart LR
    subgraph Inputs["Data on disk (YAML)"]
        Banks["banks/*.yaml<br/>bank profiles"]
        Scen["scenarios/**/*.yaml<br/>test cases"]
        Tpl["scenarios/_templates<br/>headers & bodies"]
        Certs["certs/<br/>QWAC + QSealC"]
    end

    subgraph Hosts["Entry points"]
        Cli["ApiTestTool.Cli<br/>(aqt.exe)"]
        Portal["ApiTestTool.Portal<br/>Blazor, :5280"]
    end

    Engine["ApiTestTool.Engine<br/>loader · runner · signing · OAuth · reports"]
    Mock["ApiTestTool.MockBank<br/>fake ASPSP :7443 / :7080"]

    subgraph Targets["Banks under test"]
        Real["Bank sandboxes<br/>(TBC, BoG, Liberty, ...)"]
        TbcMock["TBC public mock<br/>(Vercel)"]
    end

    Reports[("reports/ & data/runs/<br/>run.json · report.html<br/>summary.json · junit.xml")]

    Banks --> Engine
    Scen --> Engine
    Tpl --> Engine
    Certs --> Engine
    Cli --> Engine
    Portal --> Engine
    Cli -. "--with-mock" .-> Mock
    Portal -. "start/stop" .-> Mock
    Engine -- "mTLS + JWS + OAuth/PKCE" --> Mock
    Engine -- "mTLS + JWS + OAuth/PKCE" --> Real
    Engine -- HTTPS --> TbcMock
    Engine --> Reports
```

## 2. Engine internals

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

## 3. One AIS scenario at runtime (AIS-001)

```mermaid
sequenceDiagram
    autonumber
    participant R as ScenarioRunner
    participant B as Bank XS2A
    participant A as Bank Auth server
    participant U as Tester (browser)
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
```

## 4. Tech facts

| Item | Value |
|---|---|
| Runtime | .NET 10, C# latest, nullable on |
| Packages | YamlDotNet; xUnit for tests (central versions in `Directory.Packages.props`) |
| Storage | Files only: YAML in, `run.json` + reports out (no database yet) |
| Ports | Portal 5280 · MockBank 7443 (XS2A, mTLS) / 7080 (dashboard) · OAuth callback 5199 |
| Services covered | AIS (consents, accounts, balances, transactions), preflight, negative. No PIS yet |
