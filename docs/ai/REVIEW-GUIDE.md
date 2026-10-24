# Reviewing rules — guide for reviewers

Rules are drafted by AI from the standards and only count once a person approves them. Phase 3 turns approved rules
into automated tests, so **a wrong rule becomes a test that fails correct banks**. This guide is for reviewers who are
not open-banking specialists.

Where: the portal's **Rules** page (http://localhost:5280/rules) or, from a clause, the **Standards** page. Enter your
name at the top once; every decision is saved with your name, the time and the reason.

## Words you need

| Term | Georgian | Meaning |
|---|---|---|
| **ASPSP** | ამსმპ | The **bank** — the side whose API we test. Rules are about the ASPSP. |
| **TPP** | მმპ | The **third-party app** calling the bank's API. Our tool plays this role. |
| **PSU** | — | The **customer** whose accounts are accessed. |
| **Consent** | თანხმობა | The customer's permission for the app to read their accounts. |
| **SCA** | — | Strong customer authentication — the customer logging in at the bank to approve. |
| **validUntil** | — | Field in a consent request: until when the permission lasts. |
| **Error code** | — | Machine-readable reason in a bank's error response, e.g. `FORMAT_ERROR`, `PERIOD_INVALID`. |

## What the tool already checked

Every rule card lists the tool's checks under the rule:

- 🟢 **"Quote found in the clause."** — the quoted sentence really is in the standard (exactly, apart from spacing and
  quote marks). Drafts whose quote is *not* found never reach the queue; they are listed under **invalid**.
- 🟠 **Level warnings** — the requirement level (MUST/SHOULD/MAY) is not backed by the quote's wording. Look closely.
- 🟠 **Possible duplicate of …** — a rule with the same meaning and the same fields/codes already exists.
- 🟠 **No English translation** — for Georgian clauses.

So you do **not** have to check that the quote exists. You check **what it means**.

## The five questions

1. **Does the quote say what the requirement says?**
   Read the quote (or "EN (AI translation)" below it), then the requirement sentence. The requirement must not add
   anything the quote does not say — no extra status codes, conditions or duties. *The English translation is made by
   AI: if you read Georgian, check it, especially who does what (მმპ = app, ამსმპ = bank).*

2. **Is it a duty of the bank?**
   Rules are only about what the bank's API must do. These are **not** rules:
   - text describing what the app sends or how a request looks,
   - examples, history, definitions, explanations,
   - duties of the app or the customer alone.

3. **Is the level right?** It comes from the **wording**, not from how important it seems:

   | Level | English wording | Georgian wording |
   |---|---|---|
   | **MUST** | must, shall, required, must not, not allowed | აუცილებელია, უნდა, სავალდებულო, დაუშვებელია |
   | **SHOULD** | should, recommended | უმჯობესია, რეკომენდებულია |
   | **MAY** | may, optional, **"if this function is supported"** | შეიძლება, შესაძლებელია |

   A function the standard calls optional is **MAY**, even if most banks offer it.

4. **Is it one thing, and can a test see it from outside?**
   One rule = one requirement. A good rule reads like a test: *"when the app sends X, the bank returns Y"*.
   If a rule contains two requirements, edit it down to one (the other is usually a separate draft already).

5. **Is it new?** If the card says "Possible duplicate of …", open that rule. Keep the better one.

## Decide

| Situation | Action |
|---|---|
| All five questions are fine | **Approve** |
| Something small is wrong — level, wording, two requirements in one, translation | **Edit**, save, then **Approve**. An edited quote is checked against the standard again. |
| Not a requirement on the bank, or simply wrong | **Reject** and write the reason (it helps the next draft and other reviewers) |
| You are not sure | **Leave it as a draft**. Nothing uses drafts. Ask someone who knows the domain. |

Approved and rejected rules can be **reopened** later if you change your mind; the history keeps every step.

## Worked examples (first review, 2026-10-24)

**Reject — describes a request, not a duty.**
BG-CON-001/002/003 (Berlin Group 6.3.1.2) said "the bank **must accept**" three kinds of consent request. The quotes
only describe how those requests look and contain no *must/shall* — the orange warning said so. For the global consent
the standard even says *"if this function is supported"*. Which consent types a bank offers is the bank's choice (our
bank profiles already record it). Reason: *"Describes request formats, not a duty of the bank; these consent types are
optional per bank."*

**Approve — one clear, testable duty.**
GE-CON-002 (GE-XS2A 9.1.1.10): if the app asks for validUntil `9999-12-31`, the bank **must** (აუცილებელია) shorten it
to the maximum allowed — at most 90 days — and show the shortened date when the consent is read back. Test: request
9999-12-31, read the consent, expect a date within 90 days.

**Edit, then approve — right idea, two flaws.**
GE-CON-001 (same clause) correctly says a validUntil more than 90 days ahead must be rejected with `PERIOD_INVALID`, but
it also repeated the 9999-12-31 rule, and its English translation said the *bank* specifies the duration (it is the
app, მმპ). Fix: requirement and quote down to the 90-day sentence only, translation corrected. (Its expected HTTP status
400 is fine: the Berlin Group defines PERIOD_INVALID as a 400 error, even though this clause does not say it.)

## Afterwards

- **Export approved to rules/** (button on the Rules page, or `aqt rules export`) writes the approved rules to
  `rules/*.yaml`; commit them so the approved set is versioned in git.
- Patterns in your rejections (e.g. "MUST for optional functions") are worth telling the team: they tell us what to
  improve in the drafting prompt.
