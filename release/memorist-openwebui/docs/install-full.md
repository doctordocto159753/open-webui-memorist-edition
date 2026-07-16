# Install Full

Full is a distinct runtime: PostgreSQL is the only canonical memory store,
FalkorDB is a rebuildable graph projection, and the in-memory scheduler plus
worker/import/retrieval/attachment/forget features are enabled. It is never
Lite plus a graph container.

```powershell
.\Install-Memorist.ps1 -Mode full -NonInteractive -NoBrowser
```

The installer generates a private PostgreSQL password and verifies the live
effective Core payload before reporting success. PostgreSQL and FalkorDB have
no host-published ports. Full requires about 8 GB free disk and 6 GB RAM.

After setup, configure endpoint/model/capabilities/privacy acknowledgement,
test the profile, and assign role defaults in Settings → Memorist → Processing
Nodes. Optional key values stay only in the local ACL-restricted `.env`.
