# standards/

Source documents the rules and scenarios are derived from, plus the clause index built from them.

| Path | Contents |
|---|---|
| `sources/` | Original files (PDF/HTML/YAML OpenAPI), one folder per standard and version, e.g. `sources/ge-xs2a/0.8.RC3/` |
| `sources/<std>/<ver>/source.yaml` | Where the file came from (URL, date obtained, licence, SHA-256) |
| `index/` | Generated: clauses as JSON Lines (`{standard, version, clause, title, text, page}`); rebuilt by `aqt standards ingest` |

Documents (latest versions, English preferred — see `docs/ai/PLAN.md` §8):

| Standard ID | Document | Version | Where |
|---|---|---|---|
| `BG-IG` | Berlin Group NextGenPSD2 Framework Implementation Guidelines | 1.3.16 (2025-12-02) | https://berlin-group.org/library/ |
| `BG-OAS` | NextGenPSD2 OpenAPI files | 1.3.x | https://gitlab.com/the-berlin-group/nextgenpsd2 |
| `GE-XS2A` | XS2A Standard Implementation Guide (AIS + PIS) | 0.8.RC3 (2022-09-12) | https://openfinance.ge/en/standards |
| `GE-JWS` | JSON Web Signature Implementation Guide | 0.5.RC1 | same |
| `GE-IAA` | Identification, Authentication and Authorization Manual | 0.8.RC1 | same |
| `GE-IDA` | User Identification and Authentication Service Standard | 0.3.RC3 | same |
| `NBG-80-04` | NBG Rule on Inclusion in Open Banking (Order 80/04) | — | https://nbg.gov.ge/en/page/open-banking |

The `GE-*` documents are listed as Georgian on openfinance.ge. If no English version exists, the Georgian
original is the source of truth (quotes are verified against it) and a marked machine translation is stored
next to each clause.

Status: proposal — the ingest command is not implemented yet.
