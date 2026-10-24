# ApiTestTool (`aqt`)

Standalone tool that tests a bank's **XS2A sandbox and auth server** the way a TPP would:
mutual TLS with our QWAC, requests signed with our QSealC (`Digest` + `X-Jws-Signature`),
and every response checked against what the standard expects.

- **Python:** Python 3.11+, only public PyPI packages (PyYAML, cryptography, pyOpenSSL, Flask; pytest for tests).
  The tool was first written in C#/.NET; this repository now holds the Python implementation (see
  [Moved from C#](#moved-from-c) for what changed).
- **Standalone:** no AltaSoft server models. The core tool needs no database; the optional store/AI layer
  (`pip install -e .[ai]`) adds PostgreSQL, AI run summaries and rules drafted from the standards.
- **Data-driven:** banks are YAML profiles in `banks/`, tests are YAML scenarios in `scenarios/`.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -e .[test]
pip install -e .[ai]               # optional: store (PostgreSQL), standards, AI (Claude / local Ollama)
```

This installs the `aqt` command (`python -m aqt` works too). Run it from the repository root: it finds `banks/` and
`scenarios/` there (or use `--root`, or set `AQT_HOME`).

| Path | Role |
|---|---|
| `aqt/` | Engine: YAML loading (`yamljson`, `binding`, `profiles`, `scenarios`), runner (`runner`, `steps`, `variables`, `jsonpath`), `signing`, `certificates`, `oauth`, `httpclient`, `reporting` |
| `aqt/cli.py` | `aqt` command line |
| `aqt/mockbank/` | In-memory fake ASPSP enforcing real XS2A checks |
| `aqt/portal/` | Web portal (Flask); `ai_services.py` adds the store writes, AI summary, Standards, Rules, Drafts and Coverage pages |
| `aqt/store/` | PostgreSQL store: schema migrations, `masking` (secrets/IBANs), run import, clause, rule and scenario-draft queries, `coverage` |
| `aqt/standards/` | Standards → clauses: PDF reader (table of contents driven), Word reader, quote verifier, `sources.yaml` |
| `aqt/ai/` | Model providers + audit (`chat`), run summaries, standards ingest, rule drafting and review (`rules`), scenario drafting and review (`scenario_drafting`); prompts in `ai/prompts/` |
| `aqt/dryrun.py` | Runs one scenario on an in-process MockBank (never a real bank); used to check AI drafts |
| `aqt/cli_ai.py` | `aqt db`, `aqt ai`, `aqt standards`, `aqt rules`, `aqt scenarios`, `aqt coverage` |
| `tests/` | pytest, including every shipped YAML file and an end-to-end run against MockBank |

## Web portal (`aqt/portal`)

A browser UI on top of the same engine: pick a bank and suites, start a run, watch it live, and
browse every run's results (scenarios, steps, failures, full HTTP requests/responses, report files).

```bash
aqt portal                 # http://localhost:5280/   (--port, --root <dir>, --data <dir>)
```

| Page | What it shows |
|---|---|
| **Dashboard** | All bank profiles with their last result and pass rate; MockBank status with Start/Stop |
| **New run** | Bank, suites or individual scenarios, options (built-in MockBank, automatic SCA, preflight gate) |
| **Run** | Live progress while running, a **"Bank login needed"** button when a scenario waits for SCA, then results per scenario with steps and HTTP exchanges; links to `report.html`, `summary.json`, `junit.xml`; Run again / Delete |
| **Runs** | History of all runs |
| **Scenarios** | The scenario catalogue grouped by suite |
| **Standards / Rules** | Clauses of the standards; AI rule drafts to approve, edit or reject (store/AI layer) |
| **Drafts** | AI scenario drafts from approved rules, with the MockBank dry run; approve writes the YAML file |
| **Coverage** | Approved rules → scenarios → last result per bank |

- Runs execute one at a time (manual SCA uses one fixed callback port) and queue otherwise.
- Each run is stored in its own folder (`run.json` + reports) under `reports/portal-python/`, so history survives restarts.
- Banks and scenarios are read from disk on every page load, so YAML edits and the `certs/` folder apply immediately.
- Manual SCA: the login page opens in your browser and redirects to `http://localhost:5199/callback` on
  the machine running the portal, so use the portal on the same machine (it is a local tool; it has no
  login of its own and listens on localhost only).

## Try it without any bank (MockBank)

`aqt/mockbank` is a fake bank on your machine: XS2A v2 consents and accounts, an OAuth2
server with PKCE, mutual TLS, and JWS signatures in both directions. It needs no credentials or
certificate files. MockBank generates its own certificates, and the `MOCK` profile generates
throw-away TPP certificates on every run.

```bash
aqt run --bank MOCK --with-mock --sca-auto
```

The CLI starts MockBank inside its own process (`--with-mock`), runs all suites, and stops it again. Reports are
written to `reports\` under the working folder; the console prints the full path. Or MockBank and the tool in two
windows:

```bash
aqt mock
aqt run --bank MOCK --sca-auto
```

If MockBank is already running (`aqt mock` in another window, or the portal's built-in one), `--with-mock` reuses it
instead of starting a second one.

- `--sca-auto` follows the OAuth login redirect without a browser. It only works with servers that approve
  automatically, such as MockBank. Never use it against a real bank. Without it, the login page opens in your
  browser so you can click **Approve**/**Deny**.
- MockBank settings are in `aqt/mockbank/mockbank.settings.json`: ports 7443/7080,
  `AutoApprove` (`headless` | `always` | `never`), `VerifySignatures`, `SignResponses`, `MaxFrequencyPerDay`.
  Environment variables override them, e.g. `MockBank__AutoApprove=never`.
- MockBank enforces what a real ASPSP checks (client certificate, X-Request-ID, signature and Digest,
  QSealC/QWAC organisation match, consent validation, PKCE, token-to-consent binding). So when a
  scenario fails against it, the tool or the scenario has really changed.

## TBC Open Banking mock (public sandbox)

`banks/TBC-MOCK.yaml` + the `tbc` suite test the independent TBC mock at
https://tbc-bank-mock-open-banking-api.vercel.app (docs: `/reference/aisapioverview`, spec: `/openapi.json`).
No credentials are needed.

```bash
aqt run --bank TBC-MOCK --suite preflight,tbc --sca-auto
```

In the portal: bank `TBC-MOCK`, suites `preflight` + `tbc`, "Complete SCA automatically" on.

| Scenario | Flow |
|---|---|
| TBC-AIS-001 | v1 bank-offered consent → start authorisation → OAuth (PKCE, nonce) approve → token + refresh → status `valid` → consent details → accounts with balances → balances via `_links` → booked transactions → delete |
| TBC-AIS-002 | v2 detailed consent (savings + loan IBANs from the profile) → OAuth → status `valid` → savings with balances → savings balances → loans → loan balances via `_links` → delete |
| TBC-NEG-001…005 | validUntil in the past, frequencyPerDay 5, v2 consent without services, unknown consent (CONSENT_UNKNOWN), accounts without a valid consent |

How TBC differs from the AltaSoft/MOCK API, and what the tool does about it:
- Current accounts use **v1** Berlin Group consents (`/v1/consents`, `validUntil`, `/v1/accounts`); savings/loans
  use **v2** consents with `access.savings/loans` and `consentType: detailed`. `access.payments` is rejected,
  so the generic `ais`/`negative` suites are not meant for TBC (`ais` skips itself via `requires`).
- `_links` are host-relative with the base path (`/0.8/v1/...`): the engine resolves paths starting with `/`
  against the host, so links can be followed as they are.
- SCA: the consent page has Approve/Deny and no login. `--sca-auto` (and the portal option) submits "Approve"
  on such pages; pages with a password field are never submitted.
- The mock injects random 503s on purpose: the profile turns on `limits.retry` (off by default for real
  banks); every retry is listed as a warning.
- Findings the run reports as warnings: no `registration_endpoint` / `mtls_endpoint_aliases` in discovery,
  savings accounts use `links` instead of `_links`. Also noticed (not asserted): unknown consents return 404
  where TBC's reference documents 403, and loan balance `amount` is a JSON number instead of a string.

## Quick start with a real bank sandbox

1. Put the TPP test certificates in `certs/` as PFX files (see [certs/README.md](certs/README.md)) and set
   `AQT_QWAC_PASSWORD` / `AQT_QSEALC_PASSWORD`.
2. Run from the repository root:

```bash
aqt run --bank CRTUGE22 --suite preflight --verbose
```

Reports go to `reports/<BIC>-<timestamp>/`: `report.html`, `summary.json`, `junit.xml`.

### Commands

| Command | What it does |
|---|---|
| `aqt run --bank <BIC\|file> [--suite a,b] [--scenario ID,..] [--report html,json,junit] [--out dir] [--verbose] [--no-preflight-gate] [--sca-auto] [--wait <s>] [--with-mock]` | Runs scenarios against one bank |
| `aqt mock` | Runs MockBank until Ctrl+C |
| `aqt portal [--port 5280] [--data dir]` | Runs the web portal |
| `aqt list banks` / `aqt list scenarios [--suite x]` | Lists profiles / scenarios |
| `aqt validate` | Loads every profile, template and scenario and reports mistakes |
| `aqt version` | Prints the version |

Common options: `--root <dir>` (folder with `banks/` and `scenarios/`), `--banks <dir>`, `--scenarios <dir>`.

Exit codes: `0` all passed, `1` failures or errors in the run, `2` invalid input or configuration.

Preflight scenarios always run first. If a **Critical** preflight scenario fails (e.g. TLS or the
signature is not accepted), the other scenarios are marked **Blocked** instead of failing one by one
for the same reason. Use `--no-preflight-gate` to run them anyway.

## Bank profiles (`banks/`)

`_shared.yaml` holds settings common to every bank (TPP certificates, limits). Each `<BIC>.yaml`
`extends` it and adds the bank's endpoints, supported features and test data. `_example.yaml`
documents all fields. Files starting with `_` are not listed or run.

The 16 profiles here were created from the existing TestTool configuration (sandbox URLs) and the
SDK's `AisProvider` BICs. Discovery URLs, features and test data still need to come from each bank.

Certificates: `file` (PFX, path relative to the profile) with `passwordEnv`, or `selfSigned` (generated per run, for
MockBank and public mocks). Loading by `thumbprint` from the Windows certificate store is not supported in Python;
export the certificate with its private key to a PFX instead.

## Scenarios (`scenarios/`)

```yaml
id: NEG-CON-001
title: Create consent without PSU-IP-Address is rejected
suite: negative                 # preflight | negative | ais | pis | ...
severity: Major                 # Critical | Major | Minor | Info
specRef: "NextGenPSD2 IG 6.2; 14.11.1"
origin: TPP/Blazor/BadRequests/CreateConsentBadRequests.cs#PsuIpAddressHeaderNull
requires: [features.consents]   # skipped if the profile lacks it; `features.consents=aspspManaged` = array contains
rules: [GE-CON-001]             # optional: approved rules this scenario verifies (aqt coverage; validate warns with --db)
steps:
  - request:
      method: POST
      path: v2/consents/account-access
      headersTemplate: consent           # from scenarios/_templates
      removeHeaders: [PSU-IP-Address]
      bodyTemplate: consent.accountList  # deep-merged with `body:` if given
      signing: normal                    # normal | none | corruptSignature | wrongDigest
    expect:
      status: 400                        # or [400, 401]
      errorCode: FORMAT_ERROR            # $.code, $.additionalErrors[*].code, $.tppMessages[*].code; "*" = any
      echoRequestId: true
      # also: headers, json {path: value}, exists, notExists, warnIfMissing,
      #       contentType, maxDurationMs, responseSignature
    save: { consentId: $.consentId }     # used later as {{consentId}}
cleanup: []                              # always runs; failures become warnings
```

Other step kinds: `tls:` (handshake, protocol, server certificate), `localCertificates:`
(our own QWAC/QSealC) and `sca:` (below). Any step can have `runIf: [var]` to run only when an earlier
step saved that variable; a request can use `authOptional: true` to go without `Authorization` when no
token was obtained (used to delete a consent in cleanup even if SCA failed).

### SCA step (OAuth, manual)

```yaml
- name: SCA via OAuth (manual login)
  sca:
    mode: manual                     # tester logs in with a sandbox PSU in the system browser
    discovery: "{{scaOAuthUrl}}"     # from _links.scaOAuth; default: profile endpoints.discovery
    scope: "AIS:{{consentId}}"
    refresh: true                    # also test the refresh_token grant
    timeoutSeconds: 300
  # saves {{token}} and {{refreshToken}} (names configurable: saveAccessTokenAs / saveRefreshTokenAs)
```

What happens: discovery (a URL without `/.well-known` gets `/.well-known/oauth-authorization-server`,
as in the TestTool) → authorize URL with PKCE S256 and a random `state` → the browser opens and the
console shows the URL → a small loopback listener on `tpp.redirectUri` (default
`http://localhost:5199/callback`, no admin rights needed) waits for `code` + `state` → `state` is checked →
code exchange at `token_endpoint` over mTLS (`client_id` = our TPP id from the QWAC) → optional refresh.
Bank-specific parameters come from the profile's `quirks.oauth` (TBC: `scopeSuffix: " openid"`,
`nonce`, `uiLocales`). Codes and tokens are masked in reports.

Outcomes: auth-server errors, wrong `state` or a rejected token request are **Failed** (the bank);
no login within the timeout, a busy callback port or missing configuration are **Error** (our side).

Variables: `{{uuid}}`, `{{date.today+2d}}`, `{{datetime.now}}`, `{{bank.bic}}`, `{{tpp.redirectUri}}`,
`{{tpp.id}}`, `{{vars.*}}` (profile test data), `{{env.NAME}}`, and any value saved by a previous step.

### Included so far

| Suite | Scenarios |
|---|---|
| `preflight` | PRE-001 local certificates · PRE-002 mutual TLS · PRE-003 OAuth discovery document · PRE-004 signed request gets a standard error |
| `negative` | NEG-CON-001…008, ported from the TestTool `CreateConsentBadRequests` · NEG-SIG-001/002 broken signature and digest |
| `ais` | AIS-001 account-list consent → start authorisation → OAuth SCA (+ refresh) → status `valid` → accounts, balances · AIS-002 ASPSP-managed consent → … → accounts, balances, booked transactions. Both delete the consent in cleanup. Need `features.scaOAuth` and the consent type in the profile. |
| `tbc` | TBC-AIS-001/002, TBC-NEG-001…005 (see above) |

Running only some suites (`--suite ais`) still runs `preflight` first unless `--no-preflight-gate` is given,
so a bank that is down fails once, at preflight, and the rest is marked Blocked.

The TestTool posted some bad requests to `v2/consents`; the ported scenarios use the current path
`v2/consents/account-access`. The consent header set sends the redirect URI as `Client-Redirect-URI`
(the header the SDK and XS2A use, and part of the JWS signature), not `TPP-Redirect-URI`.

## Verification

```bash
pytest                                            # 22 tests
aqt validate
aqt run --bank MOCK --with-mock --sca-auto        # 16 passed, 7 skipped (tbc suite)
aqt run --bank TBC-MOCK --suite preflight,tbc --sca-auto   # 11/11
```

- The tests cover YAML typing and binding (unknown/missing properties), variables, JSON paths, signing and
  verification (RSA-PSS and ECDSA, corrupted signature, wrong digest, tampered header, clock skew), certificate facts,
  the authorize URL and TBC quirks, headless approval parsing, reports (summary.json round trip, JUnit, HTML),
  loading of every shipped YAML file, and an end-to-end run of all suites against MockBank on free ports (including
  preflight gating and an unreachable bank).
- Before the C# code was removed, the Python version was checked against it in both directions: the Python client
  against the C# MockBank and the C# client against the Python MockBank both passed 16/16, so signatures, digests,
  mTLS and the OAuth flow are compatible. Reports keep the C# `summary.json` shape.

## Store and AI layer

Optional, installed with `pip install -e .[ai]`; PostgreSQL runs in Docker (`docker compose up -d`, localhost:5435).
Everything is masked before it is stored or sent to a model (`Authorization`/`PSU-ID` headers, OAuth codes and tokens
in JSON, forms and URLs, IBANs), and every model call is a row in `ai_calls`.

```bash
aqt db migrate                          # create/upgrade the schema (a database created by the C# tool is kept)
aqt db import reports                   # load existing report folders
aqt run --bank MOCK --with-mock --sca-auto --db "Host=localhost;Port=5435;Database=aqt;Username=aqt;Password=aqt_dev"
aqt ai check                            # Claude (ANTHROPIC_API_KEY) and local Ollama reachable, audited
aqt ai summarize latest --provider ollama --show-input
aqt standards fetch && aqt standards ingest
aqt standards search "consent validity"
aqt rules draft GE-XS2A:0.8.RC3:9.2.2 --provider ollama
aqt rules approve GE-AUTH-001 --by "Your Name"
aqt scenarios draft GE-CON-001 --provider ollama   # needs an approved rule; dry runs on an in-process MockBank
aqt scenarios approve NEG-CON-009 --by "Your Name"  # writes scenarios/negative/NEG-CON-009-....yaml, links the rule
aqt coverage                            # approved rules -> scenarios -> last result per bank
aqt portal                              # store/AI pages on when the database is reachable (--no-db turns them off)
```

- **Run summary:** problems grouped by root cause, bank vs our side vs environment, evidence, clause, next step and
  what changed since the bank's previous run. Facts are computed in code; the answer is checked in code (unknown
  scenarios dropped, clauses must come from the run, warnings and skips are not findings) and saved in `run_summaries`.
- **Rules:** the standards in [standards/sources.yaml](standards/sources.yaml) (Berlin Group IG 1.3.16; Georgian XS2A,
  JWS, IAA guides) are split into clauses with full-text and bge-m3 (local) search. The AI drafts rules from a clause;
  the quote must occur in the clause (else `invalid`), the level must match the wording, duplicates are flagged. A
  person approves, edits or rejects on the portal's **Rules** page or with `aqt rules`; how to decide:
  [docs/ai/REVIEW-GUIDE.md](docs/ai/REVIEW-GUIDE.md).
- **Scenarios from rules:** the AI drafts a scenario YAML for one approved rule. Code sets the id, suite, severity,
  `specRef`, `origin` and `rules:`; loads the draft with the engine (templates must exist, requests only to the bank's
  XS2A API); runs it on an in-process MockBank; and sends errors and dry-run failures back to the model (at most 3
  attempts). The model may answer that MockBank does not implement the rule (`MOCK_GAP`); that is shown to the reviewer,
  who decides. A person approves, edits (checked and dry-run again) or rejects on the **Drafts** page or with
  `aqt scenarios`; approving writes the file to `scenarios/<suite>/`. **Coverage** shows each approved rule with its
  scenarios and their last result per bank.
- **Providers:** Claude (`claude-opus-5-5`, refusal fallback, effort per feature) by default; switch a feature to the
  local model with `--provider ollama` or `AQT_AI_<FEATURE>_PROVIDER=ollama` (e.g. `AQT_AI_SUMMARY_PROVIDER`).
- Database tests run when `AQT_TEST_DB` is set (`host=localhost port=5435 user=aqt password=aqt_dev`); each test
  module creates and drops its own database.

## Moved from C#

The C# solution (`src/`, `tests/`, `ApiTestTool.sln`, `Directory.*.props`, `nuget.config`, `dotnet-tools.json`) was
replaced by this Python implementation. It is in the git history (last C# commit: `8abf9f6`). Everything is ported:
the engine, CLI, MockBank, portal, and the Store, Standards and AI layers (same database schema, same prompts and
checks; the existing database works unchanged).

Smaller differences: no Windows certificate store (PFX files instead); TLS 1.2 cipher names and untrusted-certificate
messages use OpenSSL wording; the portal's run page refreshes by polling instead of a Blazor connection; the PDF text of
the Berlin Group guide comes from pdfplumber instead of PdfPig (same 182 clauses; tables and figures differ in places;
every existing rule quote still verifies).

## Not done yet

The ask-agent (Phase 4); Playwright and decoupled SCA modes, onboarding (client registration), PIS
suite, OpenAPI schema validation, Excel report, `aqt compare`, monitor mode. The AIS suite has been verified against
MockBank (all 16 scenarios pass); it has not yet been run against a real local XS2A + AuthServer or a bank sandbox.
