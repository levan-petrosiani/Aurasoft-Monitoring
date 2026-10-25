# docs/overview

`system-overview.pdf` — the system overview (architecture, what is built, how it works, the measured comparison of
Claude and the local model, what is left) as of 25 October 2026: all AI phases (0–6) and the eval sets complete. Its source
is `system-overview.html`; the comparison numbers come from the eval reports (`aqt ai eval`, `reports/evals/`).

Rebuild after editing the HTML (Chrome headless; the two Mermaid diagrams load from cdn.jsdelivr.net):

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --no-pdf-header-footer \
  --virtual-time-budget=20000 --print-to-pdf=docs/overview/system-overview.pdf \
  "file://$PWD/docs/overview/system-overview.html"
```
