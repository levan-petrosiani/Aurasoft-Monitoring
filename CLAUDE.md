# CLAUDE.md

ApiTestTool (`aqt`) tests banks' XS2A (Berlin Group NextGenPSD2 / NBG Georgia) sandboxes as a TPP:
mTLS with a QWAC, requests signed with a QSealC (`Digest` + `X-Jws-Signature`), OAuth/PKCE SCA, and
every response checked against the standard. Python 3.11+, data-driven by YAML. Hackathon project.
It was first written in C#/.NET (last C# commit `8abf9f6`); everything is ported, including the optional Store,
Standards and AI layers (`pip install -e .[ai]`).

- `README.md` — user manual · `docs/ai/PLAN.md` — AI layer plan and decisions
- `ARCHITECTURE.md` — Mermaid snapshot of the current system; **update it at the end of every phase**
- `docs/ai/REVIEW-GUIDE.md` — how reviewers approve/edit/reject rules; keep it in sync with the rule checks and the
  Rules page's "How to review" box
- `NOTE.md` — work journal; **append an entry for every step**: what was done and why (newest at the bottom)

## Commands

```bash
python -m venv .venv && .venv\Scripts\activate && pip install -e .[test]   # once
pytest                                                   # unit + end-to-end tests
aqt validate                                             # load and check every YAML file
aqt run --bank MOCK --with-mock --sca-auto               # full run, no real bank
aqt run --bank TBC-MOCK --suite preflight,tbc --sca-auto # public TBC mock
aqt mock                                                 # MockBank only (https :7443, http :7080)
aqt portal                                               # web UI on http://localhost:5280/
pip install -e .[ai] && docker compose up -d             # store/AI layer: PostgreSQL on localhost:5435
aqt db migrate|import|status                             # store
aqt ai check                                             # Claude + Ollama reachable, audited
aqt ai summarize latest --provider ollama --show-input   # AI run triage
aqt standards fetch && aqt standards ingest              # standards → clauses (+ local embeddings)
aqt rules draft <clause-key> --provider ollama           # AI rule drafts
aqt scenarios draft <approved-rule-id> --provider ollama # AI scenario draft, dry run on in-process MockBank only
aqt scenarios drafts | approve|reject <draft> --by <name>  # review scenario drafts
aqt coverage                                             # approved rules -> scenarios -> last result per bank
aqt ask "<question>" --provider ollama                   # ask-agent (read-only tools); aqt ask history
aqt monitor status|run-due|alerts|trends                 # monitoring (monitoring.yaml); aqt db prune = retention
aqt ai eval --provider ollama                            # eval sets (evals/*.yaml), report in reports/evals/
AQT_TEST_DB="host=localhost port=5435 user=aqt password=aqt_dev" pytest   # include database tests
```

On macOS with uv-created venvs, editable installs may not import (Python 3.14 skips hidden .pth files); run
`python -m aqt ...` from the repo root instead of the `aqt` script.

After changing YAML, run `validate`. After changing engine code, run `pytest` and a MOCK run.
Run `aqt` from the repo root (the CLI finds `banks/` there).

## Layout

| Path | Role |
|---|---|
| `aqt/` | Engine: `yamljson` + `binding` (YAML → dataclasses, unknown properties rejected), `profiles`, `scenarios`, `runner`, `steps` (request/tls/sca/localCertificates + expectations), `variables`, `jsonpath`, `signing` (JWS + Digest), `certificates`, `oauth` (PKCE, callback listener, headless approval), `httpclient` (mTLS, retries), `reporting`. No UI, no AI |
| `aqt/cli.py` | `aqt` command (`run`, `mock`, `portal`, `list`, `validate`, `version`) |
| `aqt/mockbank/` | In-memory fake ASPSP enforcing real XS2A checks (pyOpenSSL for optional client certificates); the reference target for new scenarios |
| `aqt/portal/` | Flask UI; `services.py` (`RunService`) queues runs and stores them in `reports/portal-python/`; `ai_services.py` writes them to the store and serves the AI summary, Standards, Rules, Drafts, Coverage and Ask pages |
| `aqt/store/` | PostgreSQL store (psycopg, plain SQL): `migrations/*.sql`, `masking.py`, `importer.py`, `store.py`, `coverage.py` |
| `aqt/monitor/` | Monitoring (needs the store): `config.py` (`monitoring.yaml`, due times), `alerts.py` (detect after each saved run, webhook), `trends.py`; the portal's `MonitorService` runs schedules |
| `aqt/dryrun.py` | `MockDryRun`: one scenario on an in-process MockBank on free ports (MOCK profile, loopback only) |
| `aqt/standards/` | No AI/DB: `pdf_reader.py` (TOC-driven, pdfplumber), `docx_reader.py`, `quote.py` (verifier), `sources.py` |
| `aqt/ai/` | `chat.py` (Claude / Ollama + audit + masking), `summaries.py`, `ingest.py`, `rules.py` (drafter, RuleBook, export), `scenario_drafting.py` (ScenarioDrafter, ScenarioReview), `agent.py` (AskAgent, AgentTools), `prompts/` |
| `aqt/cli_ai.py` | `aqt db | ai | standards | rules | scenarios | coverage | ask` (imports the optional packages only when used) |
| `tests/` | pytest: YAML, signing, reports, every shipped YAML file, end-to-end MockBank run on free ports |
| `docker-compose.yml` | PostgreSQL + pgvector for the store (localhost:5435) |
| `banks/<BIC>.yaml` | Bank profiles; `extends: _shared.yaml`; `_`-prefixed files are not run |
| `scenarios/<suite>/*.yaml` | Test cases; reusable headers/bodies in `scenarios/_templates` |
| `rules/` | Approved rules exported from the database (`aqt rules export`); the database is the source of truth |
| `standards/` | `sources.yaml` manifest (URL, version, SHA-256); downloaded files under `sources/` are git-ignored |
| `evals/` | Eval sets for the AI features (`aqt ai eval`, `aqt/ai/evals.py`): cases with known answers, scored in code |
| `docs/ai/` | AI feature plan and design notes |

## Hard constraints

- **Standalone:** only public PyPI packages, declared in `pyproject.toml` (and `requirements.txt`).
  Never reference B7.PSD2/AltaSoft projects.
- **Never commit secrets:** `certs/` contents, `*.pfx`, API keys. Keys come from environment variables
  (`AQT_QWAC_PASSWORD`, `AQT_QSEALC_PASSWORD`, `ANTHROPIC_API_KEY`).
- **`--sca-auto` is for MockBank / public mocks only** — never add it to a real bank's flow.
- Codes, tokens and other secrets are masked in reports; any new output or store must mask them too.
- Every scenario needs a `specRef` naming the clause it tests.
- Unattended (scheduled) runs never wait for a bank login: scenarios with an SCA step are skipped
  (`ScenarioRunner.skip_sca_reason`) unless SCA is automatic on MockBank. Alerts are computed in code, never by a model.
- Payment (PIS) scenarios must `require` a `variables.pis.*` test account, so they only run where a profile names the
  bank's sandbox test accounts; AI drafts that send payment data without it are refused in code (`check_scenario`).

## AI layer rules (see docs/ai/PLAN.md)

- The engine (`aqt/`) stays deterministic and AI-free. AI code lives in its own package and only
  *produces artifacts* (draft rules, draft scenarios, summaries) or *answers questions*; the engine
  executes. An AI never sends requests to a bank directly.
- AI output is a **draft** until a human approves it (`status: draft` → `approved` happens only in the
  portal review screen, recording reviewer and time). Draft scenarios never run against real banks.
- Every rule and generated scenario must cite a clause, and its verbatim `quote` must be found in the
  indexed standard text (`aqt/standards/quote.py`, checked in code, not by the model). Georgian documents stay Georgian;
  quotes are verified against the Georgian text, English translations are per rule and marked as AI-made.
- Only `RuleBook` (`aqt/ai/rules.py`) changes a rule's status, always with a reviewer name and a `rule_reviews` row.
- Only `ScenarioReview` (`aqt/ai/scenario_drafting.py`) turns a scenario draft into a file in `scenarios/`; drafts are
  dry-run only on the in-process MockBank (`aqt/dryrun.py`), and code (not the model) sets their id, suite, `specRef`,
  `origin` and `rules:`.
- The ask-agent's tools are read-only. Its SQL runs logged in as `aqt_agent` (SELECT on the `agent` views only, one
  prepared statement, read-only, timeout) — never as the main user with `SET ROLE` (the SQL could switch back). New
  data for the agent = a view in a migration; never expose headers, bodies or `ai_calls`.
- Every model call is written to the audit log (model, prompt hash, token usage, output, who triggered it).
- Data sent to the model is masked first (tokens, auth codes, PSU identifiers, IBANs as configured).
- Providers (`aqt/ai/chat.py`): Claude via the official `anthropic` SDK (`claude-opus-5-5`) or a local model via Ollama,
  per feature (`AiOptions`, env `AQT_AI_<FEATURE>_PROVIDER/_MODEL/_EFFORT`). Drafting defaults to Claude; summaries
  and the ask-agent can switch to local. Embeddings come from local Ollama `bge-m3`. Never hard-code models.
- Prompts are files in `aqt/ai/prompts/` (with their JSON schemas); review prompt changes like code, and run
  `aqt ai eval` for the affected feature before and after (eval cases in `evals/*.yaml`, scored in code, never by a model).
- Store: PostgreSQL (`docker compose up -d`, port 5435; `--db` or env `AQT_DB`). Schema changes = a new numbered SQL
  file in `aqt/store/migrations/`.
  CLI and portal runs must still work without a database — a store failure is a notice, never a failed run.
- Everything written to the store or sent to a model goes through `SensitiveDataMasker` first.
- Facts go in code, judgement goes to the model: counts, run-to-run changes, clause existence and scenario ids are
  computed or checked in code (see `build_digest`, `RunSummary.validate`); the model explains and groups.
- Claude requests go through the beta endpoint with `fallbacks: "default"` (refusal fallback) and per-feature
  effort; `tests/test_ai.py` captures the real request with a fake server — keep it passing when touching `chat.py`.
- AIS first; PIS is designed in (`service: AIS | PIS | COMMON`) — don't add anything AIS-only to shared code.

## Style

Match the existing code: dataclasses for models (YAML fields are camelCase, bound to snake_case fields by
`aqt/binding.py`), type hints, docstrings on modules and public classes, `ConfigurationError` for problems on our
side (they become `Error`, never `Failed`). Keep README tables in sync when adding commands, profile fields,
scenario fields or suites.
