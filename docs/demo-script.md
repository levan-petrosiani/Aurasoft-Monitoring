# Demo video script

For the hackathon's demo video: 2–3 minutes, voice-over required, synthetic data only, MP4 or a downloadable link.
Target length 2:50; the hard limit is 3:00, and anything longer is cut off during scoring.

- **Persona:** Nino, an integration engineer at a fintech (TPP) about to connect to Georgian banks.
- **Data (synthetic only):** TBC's public mock sandbox, and our MockBank set up as a "test bank" with three deliberately
  planted deviations. No real bank, no real customer data.

## Before recording (about 10 minutes)

1. Restart the portal so its built-in MockBank has the planted deviations: it stops checking signatures, and it allows
   up to 10 requests per day instead of 4.
   ```bash
   MockBank__VerifySignatures=false MockBank__MaxFrequencyPerDay=10 python -m aqt portal
   ```
   Expected MOCK run: 25 passed, 3 failed, 7 skipped.
   - `NEG-CON-004`: frequencyPerDay 7 is accepted (201 instead of 400).
   - `NEG-SIG-001`: a request with a corrupted signature is accepted.
   - `NEG-SIG-002`: a Digest that does not match the body is accepted.

   After recording, restart the portal without these variables.
2. Rehearse the whole flow once, so you know what the AI summary will say. It needs `.env` (`ANTHROPIC_API_KEY`) and
   the database (`docker compose up -d`).
3. Screen setup:
   - Browser at 125% zoom, 1920×1080, one window only.
   - Never show `.env`, terminal history or `certs/`.
   - Close unrelated tabs and turn on Do Not Disturb.
4. Open these tabs in advance:
   - the Dashboard;
   - `banks/TBC-MOCK.yaml` and `scenarios/negative/NEG-SIG-001-corrupt-signature.yaml` in an editor;
   - the Rules page with a Georgian draft rule showing.

## Script

| Time | Screen | Voice-over |
|---|---|---|
| **0:00–0:20** Set the scene | Portal Dashboard | "Nino is an integration engineer at a fintech. She has to connect to every Georgian bank's open-banking API, and each bank implements NBG's standard a little differently. Aura Monitoring tests a bank's API the way a real TPP would, and explains what deviates." |
| **0:20–0:50** The input | Editor: the TBC profile, then the NEG-SIG-001 scenario with its `specRef`. Portal: New run → TBC-MOCK, suites preflight + tbc, automatic SCA → Start | "The input is synthetic: TBC's public mock sandbox, and our own test bank where we planted three bugs. A bank is one YAML profile. Every test is a YAML scenario that cites the clause of the standard it checks. Let's run TBC." |
| **0:50–1:50** System at work (uncut) | The live run fills in, 11/11 in about 15 s. Open TBC-AIS-001 and show a request with `X-Jws-Signature` and a masked token. New run → MOCK (the test bank) → 3 failures. Open NEG-SIG-001: "Status 201, expected 400 or 401". Click **Summarize** to show the AI summary | "Every request goes over mutual TLS with our QWAC certificate and is signed with our QSealC, with an OAuth login using PKCE. Tokens are masked in every report. TBC: eleven of eleven. Now our test bank: three failures. It accepted a request with a corrupted signature, which is a security hole, and a consent above the daily limit. The AI summary groups the failures, says whether it's the bank's side or ours, and suggests the next step. The counts come from code, not the model." |
| **1:50–2:30** Track evidence | **Compare** page: the overview, the latency chart, the deviations with their clauses, the "5/5" column. Then **Rules**: a Georgian clause with an AI-drafted rule and its verified quote; type a reviewer name and click **Approve** | "Compare puts banks side by side: compliance, latency per operation, TBC at 323 milliseconds median, and every deviation with its clause. '5 of 5' shows the results are reproducible. New checks come from the standard itself: AI drafts a rule from the Georgian text, code verifies the quote really exists, and a person approves it by name. Every AI call is audited." |
| **2:30–2:50** Output & impact | `report.html` from the run, then the Compare page again | "What Nino gets: a report per bank she can hand to the bank's team, JUnit for CI, and monitoring that alerts on regressions. A full suite against a bank takes about fifteen seconds, where checking by hand takes [manual baseline]. One tool for TPPs, banks and NBG." |

The narration is about 330 words, around 2:30 at a calm pace, which leaves room for the screen to catch up.

## Recording tips

- Record the screen uncut, as the guidelines prefer. Recording the voice separately and laying it over is fine.
- 0:50–1:50 is the tightest segment. If the AI summary takes more than about 10 s to appear, drop the TBC request
  detail.
- Export as MP4 and check the length is under 3:00. Test the download link in an incognito window before submitting.

## Still open

- `[manual baseline]`: fill in a realistic estimate of how long checking one bank by hand takes, or drop the
  comparison. Don't use a number you can't back up.
- The figures quoted (11/11 in 5 of 5 runs, p50 323 ms, about 15 s per TBC suite) are from runs on 25 October 2026.
  If a rehearsal shows different numbers, say those instead.
