# certs/

Put the TPP test certificates registered with the banks' sandboxes here (this folder is git-ignored):

| File | Used for | Password variable |
|---|---|---|
| `qwac.pfx` | mutual TLS (QWAC) | `AQT_QWAC_PASSWORD` |
| `qsealc.pfx` | request signatures, `X-Jws-Signature` (QSealC) | `AQT_QSEALC_PASSWORD` |

Paths and variable names are set in `banks/_shared.yaml`. A bank that issued its own sandbox
certificates can override them in its profile. You can also use the Windows certificate store
(`store` + `thumbprint`) instead of files.
