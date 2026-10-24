# standards/

The standards rules are drafted from.

| Path | Contents |
|---|---|
| `sources.yaml` | Manifest: id, version, language, format, download URL, **SHA-256**, licence, parsing hints |
| `sources/<id>/<version>/<file>` | The downloaded documents (git-ignored: licences differ and the files are large) |

```bash
dotnet run --project src/ApiTestTool.Cli -- standards fetch    # download missing files, verify SHA-256
dotnet run --project src/ApiTestTool.Cli -- standards ingest   # split into clauses, store with embeddings
```

| ID | Document | Version | Language | Clauses |
|---|---|---|---|---|
| `BG-IG` | Berlin Group NextGenPSD2 XS2A Implementation Guidelines | 1.3.16 (2025-12-02) | en | 182 |
| `GE-XS2A` | XS2A Standard Implementation Guide (Georgia) | 0.8.RC3 | ka | 133 |
| `GE-JWS` | JSON Web Signature Implementation Guide (Georgia) | 0.5.RC1 | ka | 32 |
| `GE-IAA` | Identification, Authentication and Authorization Manual (Georgia) | 0.8.RC1 | ka | 32 |

How documents are split:

- **PDF (Berlin Group):** the table of contents gives the clause list; each entry is found, in order, as a heading in
  the body. Deeper sections (6.3.1.2) missing from the TOC are found inside their parent, accepting only the expected
  next number. Running page headers are removed (`pageHeaderEnd`). Headings the document prints wrongly are corrected
  in the manifest (`titles`), e.g. a stray sentence glued into the BG-IG chapter 6 heading.
- **Word (Georgian guides):** Heading1–6 styles; numbers are computed like Word's automatic numbering. The "Contents"
  heading is numbered as chapter 1 in these documents, which was verified against the published PDF of GE-XS2A.
- The Georgian guides are published only in Georgian. The Georgian text is the source of truth (quotes are verified
  against it); rules carry an English translation of their quote, marked as an AI translation.
- A file whose SHA-256 differs from the manifest is refused: review the new file, then update `sources.yaml`.
