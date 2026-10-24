# docs/overview

`system-overview.pdf` — the system overview (architecture, what is built, how it works, roadmap) as of the end of
Phase 2, after the move to Python. Its source is `system-overview.html`.

Rebuild after editing the HTML (Chrome headless; the one remaining Mermaid diagram loads from cdn.jsdelivr.net):

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --no-pdf-header-footer \
  --virtual-time-budget=20000 --print-to-pdf=docs/overview/system-overview.pdf \
  "file://$PWD/docs/overview/system-overview.html"
```
