# Demo video script

Follows the storyboard in the hackathon's Demo Evaluation Guidelines (page 2) and the requirements on page 3:
- 2–3 minutes (anything longer is cut off during scoring); target 2:45;
- an MP4 or a downloadable link (not streaming-only); test it in an incognito window;
- an uncut screen capture is preferred; voice-over is required (silent videos are penalised);
- synthetic data only, since videos may be published.

On Demo Day the video plays straight after the pitch, with no introduction, so it has to stand on its own.

- **Persona:** a TPP integration team about to integrate with TBC Bank.
- **Scenario:** before writing any code, find out how TBC's API differs from the standard.
- **Data:** TBC's public mock sandbox and our MockBank. Both are synthetic, and the video says so.

## Before recording (about 15 minutes)

1. **Start the portal with one planted bug.** MockBank then allows 10 consent requests a day instead of 4. Signatures
   are still verified, so the security test shows a refusal.
   ```bash
   pkill -f "aqt portal"
   MockBank__MaxFrequencyPerDay=10 python -m aqt portal
   ```
   Expected MOCK run: 27 passed, 1 failed (NEG-CON-004), 7 skipped. TBC: 11 passed with 3 warnings.
   After recording, restart without the variable.
2. **Rehearse once and keep that MOCK run.** Click **Summarize** on it, so the AI summary is ready to open in the video
   (a live model call can take 10–20 s). Also note a draft rule on the **Rules** page to approve on camera.
3. **TBC runs:** always choose **preflight + tbc**. All suites makes TBC look like 85.7%, because the generic tests
   don't fit its sandbox.
4. **Screen setup:**
   - 1920×1080, browser at 125% zoom, Do Not Disturb on.
   - Never show `.env`, `certs/` or terminal history.
   - Open tabs in this order:
     1. Dashboard;
     2. `banks/TBC-MOCK.yaml` in an editor;
     3. Scenarios page;
     4. New run;
     5. Compare;
     6. Rules.
5. **Caption:** add a text overlay for 0:20–0:50: "Synthetic data only: TBC public mock sandbox + our MockBank" (in
   the recorder, or as a card in the editor).

## Script

| Time | Their instruction | Screen | Voice-over |
|---|---|---|---|
| **0:00–0:20** | Workflow, persona, scenario | Portal Dashboard | "A TPP integration team is about to connect to TBC Bank. Before writing any code, they want to know how TBC's open-banking API differs from the standard. This is Aura Monitoring, our Auditor Tool, and this is that check." |
| **0:20–0:50** | The input; flag what is synthetic | Overlay: *Synthetic data only*. `banks/TBC-MOCK.yaml` (endpoints, features). Scenarios page, `tbc` suite with its `specRef`s. New run → **TBC-MOCK** → **preflight + tbc** → **automatic SCA** → **Start** | "Everything here is synthetic: TBC's public mock sandbox and our own MockBank, with no real customer data. A bank is one YAML profile: its endpoints and features. Each test is a YAML scenario that names the clause it checks. We pick TBC, the preflight and TBC suites, and start." |
| **0:50–1:50** | Uncut capture: API calls, processing, steps | The TBC run live (about 15 s) → 11 passed, 3 warnings. Open **TBC-AIS-001**: consent → OAuth discovery and token → accounts, balances, transactions, each with its time in ms. Expand one request: `X-Jws-Signature`, `Digest`, masked token. Then **New run → MOCK** (built-in MockBank, automatic SCA) → PIS-001 to 005, payments created, authorised and polled to ACSC | "The whole run takes about fifteen seconds, uncut. Every call is made the way a licensed TPP makes it: mutual TLS with our QWAC certificate, each request signed with our QSealC, and an OAuth login with PKCE. Consent, accounts, balances, transactions: each request is timed and checked against the standard, and tokens are masked in every report. Eleven of eleven checks ran end to end. TBC's sandbox has no payments, so we also run our MockBank: domestic, own-bank and foreign payments, created, authorised and followed to their final status." |
| **1:50–2:30** | Compliance, security, latency, cross-bank: shown, not asserted | 1. TBC run → **TBC-AIS-002** warning `$.accounts[0]._links is missing`, with its spec reference; then **PRE-003**'s two warnings with RFC 8414 §2 and RFC 8705 §5. 2. MOCK run → **NEG-SIG-001** passed: corrupted signature refused. 3. **Compare** → overview (p50 / p95) and the latency chart, MockBank vs TBC. 4. *If time allows:* MOCK run → **NEG-CON-004** failed → the prepared **AI summary**; **Rules** → type a name → **Approve** | "A real deviation: TBC's savings accounts come back without _links, while its loans have them. Berlin Group puts links in _links, so a TPP can't reach savings balances without a workaround. The fix is to return _links, as for loans. Two optional discovery fields are flagged too, each with its RFC. Security, tested on MockBank: a corrupted signature is refused. Compare puts the banks side by side: TBC at 323 milliseconds median, 559 at p95. *[If time allows:]* The one planted bug, explained by AI with its clause. And new rules stay drafts until a person approves them." |
| **2:30–2:50** | Final artefact; quantify the gain | The TBC run's `report.html`, then the Compare overview | "What the team takes away: the Incompatibility Report, every deviation with its clause and a fix, and the Quality Report, banks side by side. Three deviations flagged on TBC, and the full check in about fifteen seconds instead of testing each endpoint by hand. The next bank is one YAML profile. Aura Monitoring." |

The narration is about 380 words, around 2:35 at a calm pace (2:45 with the optional AI lines). If a rehearsal runs
long, drop the AI lines first, then the expanded request in 0:50–1:50.

## Rules for the voice-over

- Say only what the screen shows. The numbers come from the runs on 25 October 2026: 11/11 in 5 of 5 TBC runs, p50
  323 ms, p95 559 ms, about 15 s per TBC suite. If a rehearsal shows different numbers, say those.
- No figure for manual hours: we never measured one. "Instead of testing each endpoint by hand" is the comparison we
  can stand behind.
- Call MockBank's failure a *planted* bug, never a bank's.

## After recording

- Export as MP4 and check the length is between 2:00 and 3:00.
- Upload it, open the link in an incognito window, and check it **downloads**.
- Restart the portal without `MockBank__MaxFrequencyPerDay`.
