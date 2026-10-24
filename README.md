# ApiTestTool (`aqt`)

Standalone tool that tests a bank's **XS2A sandbox and auth server** the way a TPP would:
mutual TLS with our QWAC, requests signed with our QSealC (`Digest` + `X-Jws-Signature`),
and every response checked against what the standard expects.

- **Standalone:** no database, no AltaSoft server models, only public NuGet packages (YamlDotNet).
  Its own `Directory.Build.props`, `Directory.Packages.props` and `nuget.config` keep it independent
  of the B7.PSD2 repository, so the folder can be moved to its own repo as-is.
- **Data-driven:** banks are YAML profiles in `banks/`, tests are YAML scenarios in `scenarios/`.
- Design and roadmap: [docs/api-quality/standalone-test-tool.ka.md](../../docs/api-quality/standalone-test-tool.ka.md)

## Web portal (`src/ApiTestTool.Portal`)

A browser UI on top of the same engine: pick a bank and suites, start a run, watch it live, and
browse every run's results (scenarios, steps, failures, full HTTP requests/responses, report files).

**Start it:** set **ApiTestTool.Portal** as the startup project (profile `Portal`) and press F5, or:

```bash
dotnet run --project src/ApiTestTool.Portal --launch-profile Portal
```

Then open http://localhost:5280/.

| Page | What it shows |
|---|---|
| **Dashboard** | All bank profiles with their last result and pass rate; MockBank status with Start/Stop |
| **New run** | Bank, suites or individual scenarios, options (built-in MockBank, automatic SCA, preflight gate) |
| **Run** | Live progress while running, a **"Bank login needed"** button when a scenario waits for SCA, then results per scenario with steps and HTTP exchanges; links to `report.html`, `summary.json`, `junit.xml`; Run again / Delete |
| **Runs** | History of all runs |
| **Scenarios** | The scenario catalogue grouped by suite |

- Runs execute one at a time (manual SCA uses one fixed callback port) and queue otherwise.
- Each run is stored in its own folder (`run.json` + reports), so history survives restarts.
  Development: `reports/portal/`; published: `data/runs/` next to the portal (setting `ApiTestTool:DataDirectory`).
- Banks and scenarios are read from disk on every page load. In Development the portal uses this folder
  (`ApiTestTool:Root` = `../..`), so YAML edits and the `certs/` folder apply immediately.
- Manual SCA: the login page opens in your browser and redirects to `http://localhost:5199/callback` on
  the machine running the portal, so use the portal on the same machine (it is a local tool; it has no
  login of its own and listens on localhost only).

## Store and AI layer (PostgreSQL, Claude / Ollama)

Every run can also be written to PostgreSQL: runs, scenario and step results, HTTP exchanges and events. The
AI features use that data (see [docs/ai/PLAN.md](docs/ai/PLAN.md)). Before anything is stored, secrets are masked:
`Authorization`/`PSU-ID` headers, OAuth codes and tokens in JSON, form bodies and query strings, and IBANs
(only the country, check digits and last 4 characters are kept). Bodies over 64 KB are cut.

```bash
docker compose up -d                                        # PostgreSQL 17 + pgvector on localhost:5435
dotnet run --project src/ApiTestTool.Cli -- db migrate      # create/upgrade the schema
dotnet run --project src/ApiTestTool.Cli -- db import reports   # load existing report folders
dotnet run --project src/ApiTestTool.Cli -- db status
```

- **CLI:** `aqt run` saves the run when `--db <connection>` or `AQT_DB` is set. If the database is down,
  the run still passes or fails on its own merits, prints a notice, and the folder can be imported later.
- **Portal:** in Development it uses `ConnectionStrings:Aqt` (the docker-compose database), migrates on
  startup, and writes runs plus events (`run.queued/started/completed/...`, `scenario.completed`,
  `run.deleted`) in the background.
- Tables (snake_case, easy to query): `runs`, `scenario_results`, `step_results`, `exchanges` (headers as
  `jsonb`), `events`, `ai_calls`. Migrations: `dotnet tool restore` then `dotnet ef migrations add <Name>
  --project src/ApiTestTool.Store --output-dir Migrations`.
- **AI providers:** `aqt ai check` sends a one-line prompt to Claude and to local Ollama, and every call
  (including failed ones) goes to `ai_calls`. Claude needs `ANTHROPIC_API_KEY` (or `ant auth login`). Never put
  the key in `appsettings.json`. Provider and model per feature (read by the portal from Phase 1 on):
  `ApiTestTool:AI:Features:<summary|ask|ruleDrafting|scenarioDrafting>:{Provider,Model}`.
- **AI run summary (Phase 1):** `aqt ai summarize <run-id|latest> [--provider anthropic|ollama] [--model x]
  [--show-input]`, or the **AI summary** panel on a run's page in the portal ("Summarize with Claude" /
  "Local"). It groups problems by root cause, says whether the bank, our side or the environment is at fault,
  quotes the evidence, gives a next step, and lists what changed since the bank's previous run. Before it is
  stored, code checks the answer: unknown scenario ids are dropped, clause references must come from the run
  (shortened ones are restored to the run's wording), findings about passed scenarios become warnings,
  unexplained failures are listed, and the "since the previous run" text is written in code. Summaries are kept
  in `run_summaries` and linked to their `ai_calls` row. Automatic summaries after each portal run:
  `ApiTestTool:AI:Summary:AutoSummarize` (off by default). Local default model: `qwen3:8b` (~15–35 s per run);
  `gemma4:26b` is as accurate but takes ~3 min.
- **Standards and rules (Phase 2):** the standards in [standards/sources.yaml](standards/sources.yaml) (Berlin Group
  NextGenPSD2 IG 1.3.16; the Georgian XS2A, JWS and IAA guides) are split into numbered clauses and stored with
  full-text search (English stemming; plain words for Georgian) and local `bge-m3` embeddings, so an English question
  also finds Georgian clauses. From a clause, the AI drafts **rules**: one checkable requirement each, with level
  (MUST/SHOULD/MAY), an English requirement, the **verbatim quote** it rests on (plus an AI translation for Georgian)
  and how a TPP test would check it. Code checks every draft: the quote must occur in the clause (otherwise the rule
  is stored as `invalid`), a level not backed by the quote's wording is flagged, and near-duplicates (same meaning
  *and* same fields/codes) are flagged. A person approves, edits or rejects each draft in the portal (**Standards** and
  **Rules** pages, with the reviewer's name recorded) or with `aqt rules approve|reject`; `aqt rules export` writes
  approved rules to `rules/` for git. How to decide: [docs/ai/REVIEW-GUIDE.md](docs/ai/REVIEW-GUIDE.md).

  ```bash
  dotnet run --project src/ApiTestTool.Cli -- standards fetch      # download + verify SHA-256 (files are not committed)
  dotnet run --project src/ApiTestTool.Cli -- standards ingest     # clauses + embeddings into PostgreSQL (~30 s)
  dotnet run --project src/ApiTestTool.Cli -- standards search "consent validity"
  dotnet run --project src/ApiTestTool.Cli -- rules draft GE-XS2A:0.8.RC3:9.1.1.10 --provider ollama
  dotnet run --project src/ApiTestTool.Cli -- rules approve GE-CON-002 --by "Your Name"
  ```
- Database tests run when `AQT_TEST_DB` is set (e.g. `Host=localhost;Port=5435;Username=aqt;Password=aqt_dev`);
  each test class creates and drops its own database.

## Try it without any bank (MockBank)

`src/ApiTestTool.MockBank` is a fake bank on your machine: XS2A v2 consents and accounts, an OAuth2
server with PKCE, mutual TLS, and JWS signatures in both directions. It needs no credentials or
certificate files. MockBank generates its own certificates, and the `MOCK` profile generates
throw-away TPP certificates on every run.

**From Visual Studio / Rider — one project, F5**

1. Open `ApiTestTool.sln`, right-click **ApiTestTool.Cli** and choose **Set as Startup Project**.
2. Choose the launch profile **`aqt: MOCK - all suites`** in the Start button dropdown and press **F5**.
   The CLI starts MockBank inside its own process (`--with-mock`), runs all suites, and stops it again.
   Reports are written to `reports\` under the working folder; the console prints the full path.

Other launch profiles of ApiTestTool.Cli:

| Profile | What it does |
|---|---|
| `aqt: MOCK - preflight` / `- negative` | One suite against the embedded MockBank |
| `aqt: MOCK - ais (log in in the browser)` | Opens MockBank's login page in your browser so you can click **Approve**/**Deny** |
| `aqt: mock bank only` | Just runs MockBank (dashboard at http://localhost:7080/) until you stop it |
| `aqt: validate`, `aqt: list scenarios`, `aqt: help` | Checks/lists the YAML files |

If MockBank is already running (for example the **ApiTestTool.MockBank** project, or `aqt mock` in
another window), `--with-mock` reuses it instead of starting a second one.

**From a terminal** (in this folder):

```bash
dotnet run --project src/ApiTestTool.Cli -- run --bank MOCK --with-mock --sca-auto
```

or MockBank and the tool in two windows:

```bash
dotnet run --project src/ApiTestTool.Cli -- mock
```

```bash
dotnet run --project src/ApiTestTool.Cli -- run --bank MOCK --sca-auto
```

- `--sca-auto` follows the OAuth login redirect without a browser. It only works with servers that approve
  automatically, such as MockBank. Never use it against a real bank.
- MockBank settings are in `src/ApiTestTool.MockBank/mockbank.settings.json`: ports 7443/7080,
  `AutoApprove` (`headless` | `always` | `never`), `VerifySignatures`, `SignResponses`, `MaxFrequencyPerDay`.
- MockBank enforces what a real ASPSP checks (client certificate, X-Request-ID, signature and Digest,
  QSealC/QWAC organisation match, consent validation, PKCE, token-to-consent binding). So when a
  scenario fails against it, the tool or the scenario has really changed.

## TBC Open Banking mock (public sandbox)

`banks/TBC-MOCK.yaml` + the `tbc` suite test the independent TBC mock at
https://tbc-bank-mock-open-banking-api.vercel.app (docs: `/reference/aisapioverview`, spec: `/openapi.json`).
No credentials are needed.

```bash
dotnet run --project src/ApiTestTool.Cli -- run --bank TBC-MOCK --suite preflight,tbc --sca-auto
```

In Visual Studio: ApiTestTool.Cli, profile `aqt: TBC mock (Vercel) - preflight + tbc`. In the portal: bank
`TBC-MOCK`, suites `preflight` + `tbc`, "Complete SCA automatically" on.

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

Verified: 3 consecutive CLI runs and one portal run, 11/11 passed.

## Quick start with a real bank sandbox

```bash
dotnet build ApiTestTool.sln
```

1. Put the TPP test certificates in `certs/` (see [certs/README.md](certs/README.md)) and set
   `AQT_QWAC_PASSWORD` / `AQT_QSEALC_PASSWORD`.
2. Run from this folder:

```bash
dotnet run --project src/ApiTestTool.Cli -- run --bank CRTUGE22 --suite preflight --verbose
```

Reports go to `reports/<BIC>-<timestamp>/`: `report.html`, `summary.json`, `junit.xml`.

### Commands

| Command | What it does |
|---|---|
| `aqt run --bank <BIC\|file> [--suite a,b] [--scenario ID,..] [--report html,json,junit] [--out dir] [--verbose] [--no-preflight-gate]` | Runs scenarios against one bank |
| `aqt list banks` / `aqt list scenarios [--suite x]` | Lists profiles / scenarios |
| `aqt validate` | Loads every profile, template and scenario and reports mistakes |
| `aqt db migrate\|status` | Creates/upgrades the PostgreSQL store; shows row counts and pending migrations |
| `aqt db import [folder...] [--replace]` | Loads existing report folders (default `./reports`) into the store |
| `aqt ai check [--provider anthropic\|ollama] [--model x]` | One-line call to each AI provider, recorded in `ai_calls` |
| `aqt ai summarize <run-id\|latest> [--bank BIC] [--provider ..] [--model ..] [--show-input]` | AI triage of a stored run, saved in `run_summaries` |
| `aqt standards fetch\|ingest [--standard ID] [--no-embed]` | Download/verify the standards; split into clauses and store them (with embeddings) |
| `aqt standards list\|search "<text>"\|show <clause-key>` | Browse and search clauses (keys look like `BG-IG:1.3.16:6.3.1.2`) |
| `aqt rules draft <clause-key> [--provider ..] [--model ..]` | AI drafts rules from a clause; checked in code; stored as drafts |
| `aqt rules list [--status s] [--standard ID]\|show <id>` | Rules and their review history |
| `aqt rules approve\|reject\|reopen <id> --by <name> [--note ..]`, `aqt rules export` | Review; write approved rules to `rules/` |
| `aqt version` | Prints the version |

Exit codes: `0` all passed, `1` failures or errors in the run, `2` invalid input or configuration.

Preflight scenarios always run first. If a **Critical** preflight scenario fails (e.g. TLS or the
signature is not accepted), the other scenarios are marked **Blocked** instead of failing one by one
for the same reason. Use `--no-preflight-gate` to run them anyway.

### Single-file build

```bash
dotnet publish src/ApiTestTool.Cli -c Release -r win-x64 --self-contained -p:PublishSingleFile=true -o publish/win-x64
```

`publish/win-x64` then contains `aqt.exe` plus `banks/` and `scenarios/`.

## Bank profiles (`banks/`)

`_shared.yaml` holds settings common to every bank (TPP certificates, limits). Each `<BIC>.yaml`
`extends` it and adds the bank's endpoints, supported features and test data. `_example.yaml`
documents all fields. Files starting with `_` are not listed or run.

The 16 profiles here were created from the existing TestTool configuration (sandbox URLs) and the
SDK's `AisProvider` BICs. Discovery URLs, features and test data still need to come from each bank.

## Scenarios (`scenarios/`)

```yaml
id: NEG-CON-001
title: Create consent without PSU-IP-Address is rejected
suite: negative                 # preflight | negative | ais | pis | ...
severity: Major                 # Critical | Major | Minor | Info
specRef: "NextGenPSD2 IG 6.2; 14.11.1"
origin: TPP/Blazor/BadRequests/CreateConsentBadRequests.cs#PsuIpAddressHeaderNull
requires: [features.consents]   # skipped if the profile lacks it; `features.consents=aspspManaged` = array contains
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

Running only some suites (`--suite ais`) still runs `preflight` first unless `--no-preflight-gate` is given,
so a bank that is down fails once, at preflight, and the rest is marked Blocked.

The TestTool posted some bad requests to `v2/consents`; the ported scenarios use the current path
`v2/consents/account-access`. The consent header set sends the redirect URI as `Client-Redirect-URI`
(the header the SDK and XS2A use, and part of the JWS signature), not `TPP-Redirect-URI`.

## Verification

- `dotnet test` runs 122 tests (Phase 2 adds the PDF/Word clause readers, the quote verifier, the sources
  manifest, level and duplicate checks, drafting and the review workflow, clause search). Before Phase 2 it ran 90 tests (store and AI tests: masking, run mapping, import, the AI audit client,
  provider choice, the exact Claude request (captured offline), run digest, summary parsing and validation; with
  `AQT_TEST_DB`, also PostgreSQL round trips and the summarizer end to end). The 53 engine tests cover signing/verification, YAML typing, variables, JSON paths, the
  runner against an in-memory bank, preflight gating, loading of every shipped YAML file, PKCE (RFC 7636
  vector), authorize URL and TBC quirks, the callback listener (callback, auth-server error, timeout,
  busy port), and the shipped AIS-001/AIS-002 scenarios end to end against an in-memory XS2A + OAuth
  server (PKCE verified, every XS2A request signature verified, wrong state, token rejection, timeout).
- The signing code was cross-checked against the production `AltaSoft.PSD2.Crypto` code:
  `JwsDecoder` accepts our signatures and rejects the corrupted ones, and our verifier accepts
  `JwsEncoder` output.

## Not done yet

Playwright and decoupled SCA modes, onboarding (client registration), PIS suite, OpenAPI schema
validation, Excel report, `aqt compare`, monitor mode. The AIS suite has been verified against the
in-memory test server and against MockBank (all 16 scenarios pass); it has not yet been run against a
real local XS2A + AuthServer or a bank sandbox. See the plan in the design doc.
