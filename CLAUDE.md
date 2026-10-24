# CLAUDE.md

ApiTestTool (`aqt`) tests banks' XS2A (Berlin Group NextGenPSD2 / NBG Georgia) sandboxes as a TPP:
mTLS with a QWAC, requests signed with a QSealC (`Digest` + `X-Jws-Signature`), OAuth/PKCE SCA, and
every response checked against the standard. Python 3.11+, data-driven by YAML. Hackathon project.
It was first written in C#/.NET (last C# commit `8abf9f6`); the Store, AI and Standards layers are not ported yet
(see README "Moved from C#").

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
```

After changing YAML, run `validate`. After changing engine code, run `pytest` and a MOCK run.
Run `aqt` from the repo root (the CLI finds `banks/` there).

## Layout

| Path | Role |
|---|---|
| `aqt/` | Engine: `yamljson` + `binding` (YAML → dataclasses, unknown properties rejected), `profiles`, `scenarios`, `runner`, `steps` (request/tls/sca/localCertificates + expectations), `variables`, `jsonpath`, `signing` (JWS + Digest), `certificates`, `oauth` (PKCE, callback listener, headless approval), `httpclient` (mTLS, retries), `reporting`. No UI, no AI |
| `aqt/cli.py` | `aqt` command (`run`, `mock`, `portal`, `list`, `validate`, `version`) |
| `aqt/mockbank/` | In-memory fake ASPSP enforcing real XS2A checks (pyOpenSSL for optional client certificates); the reference target for new scenarios |
| `aqt/portal/` | Flask UI; `services.py` (`RunService`) queues runs and stores them in `reports/portal-python/` |
| `tests/` | pytest: YAML, signing, reports, every shipped YAML file, end-to-end MockBank run on free ports |
| `docker-compose.yml` | PostgreSQL + pgvector for the (not yet ported) store |
| `banks/<BIC>.yaml` | Bank profiles; `extends: _shared.yaml`; `_`-prefixed files are not run |
| `scenarios/<suite>/*.yaml` | Test cases; reusable headers/bodies in `scenarios/_templates` |
| `rules/` | Approved rules exported from the database (`aqt rules export`); the database is the source of truth |
| `standards/` | `sources.yaml` manifest (URL, version, SHA-256); downloaded files under `sources/` are git-ignored |
| `docs/ai/` | AI feature plan and design notes |

## Hard constraints

- **Standalone:** only public PyPI packages, declared in `pyproject.toml` (and `requirements.txt`).
  Never reference B7.PSD2/AltaSoft projects.
- **Never commit secrets:** `certs/` contents, `*.pfx`, API keys. Keys come from environment variables
  (`AQT_QWAC_PASSWORD`, `AQT_QSEALC_PASSWORD`, `ANTHROPIC_API_KEY`).
- **`--sca-auto` is for MockBank / public mocks only** — never add it to a real bank's flow.
- Codes, tokens and other secrets are masked in reports; any new output or store must mask them too.
- Every scenario needs a `specRef` naming the clause it tests.

## AI layer rules (see docs/ai/PLAN.md)

The AI, Store and Standards layers are **not ported to Python yet**. These rules apply when they are; class names
below refer to the C# implementation in git history (`8abf9f6`).

- The engine (`aqt/`) stays deterministic and AI-free. AI code lives in its own package and only
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

Match the existing code: dataclasses for models (YAML fields are camelCase, bound to snake_case fields by
`aqt/binding.py`), type hints, docstrings on modules and public classes, `ConfigurationError` for problems on our
side (they become `Error`, never `Failed`). Keep README tables in sync when adding commands, profile fields,
scenario fields or suites.
