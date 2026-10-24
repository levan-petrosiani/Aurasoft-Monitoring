# rules/

Normative rules taken from the standards (Berlin Group NextGenPSD2, Georgian openfinance.ge guides, NBG rules). A rule is one checkable
requirement with the exact clause and quote it comes from. Scenarios reference the rules they verify
(`rules: [GE-CON-014]`), which gives a coverage matrix: clause → rules → scenarios → last result.

- AI drafts rules with `status: draft`. Only a human review in the portal sets `approved`.
- `source.quote` must appear verbatim in the indexed clause text (checked in code).
- `_example.yaml` documents every field. Files starting with `_` are not loaded.
- One file per rule: `rules/<standard>/<ID>.yaml`, e.g. `rules/ge/GE-CON-014.yaml`.

Status: format proposal — not loaded by the tool yet. See `docs/ai/PLAN.md`.
