# Architecture

Snapshot of the system as it is **now**. Update this file at the end of every phase (see `docs/ai/PLAN.md`).
Diagrams are Mermaid (render in GitHub, GitLab, Rider, VS Code).

**Last updated:** 2026-10-24, after the move from C# to Python. The engine, CLI, MockBank and portal are Python.
The Store (PostgreSQL), AI (run summaries, rule drafting) and Standards layers of Phases 0–2 existed only in C#
(git history, last C# commit `8abf9f6`) and are not ported yet.

## 1. Components

```mermaid
flowchart LR
    subgraph Inputs["Data on disk (YAML)"]
        Banks["banks/*.yaml<br/>bank profiles"]
        Scen["scenarios/**/*.yaml<br/>test cases"]
        Certs["certs/<br/>QWAC + QSealC (PFX)"]
    end

    subgraph Hosts["Entry points"]
        Cli["aqt/cli.py (aqt)<br/>run · mock · portal · list · validate"]
        Portal["aqt/portal (Flask :5280)<br/>RunService → reports/portal-python"]
    end

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
```

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

## 4. Tech facts

- Python 3.11+; PyYAML, cryptography, pyOpenSSL, Flask; pytest.
- Ports: portal 5280, MockBank 7443 (HTTPS, mTLS) and 7080 (HTTP pages), OAuth callback 5199.
- Reports keep the C# `summary.json` shape (camelCase, nulls omitted); interop with the C# client and MockBank was
  verified in both directions before the C# code was removed.
