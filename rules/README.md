# rules/

Approved rules, exported from the database with `aqt rules export` (or "Export approved to rules/" on the portal's
Rules page). A rule is one checkable requirement taken from a clause of a standard, with the exact passage it rests on.

- The **database is the source of truth** (`rules` and `rule_reviews` tables): drafts, review status and the full
  history live there. These files are a versioned copy of the approved set, so changes show up in git.
- Rules are drafted by AI from a clause (`aqt rules draft <clause-key>` or the Standards page). Code checks each
  draft: the `quote` must occur in the clause text (else `invalid`), the level must be backed by the quote's wording,
  and near-duplicates are flagged. Only a person approves (`aqt rules approve <id> --by <name>` or the Rules page).
- How to review (for non-specialists): [docs/ai/REVIEW-GUIDE.md](../docs/ai/REVIEW-GUIDE.md).
- One file per rule: `rules/<prefix>/<ID>.yaml`, e.g. `rules/ge/GE-CON-001.yaml`. Files starting with `_` are kept.
- IDs: `<BG|GE|NBG>-<AREA>-<NNN>`; areas CON, ACC, TRX, PIS, PST, PCN, SBK, AUTH, SIG, TLS, ERR, HDR, GEN.
- `_example.yaml` shows the format. Do not edit exported files by hand: edit the rule in the portal and export again.
