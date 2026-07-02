# Memorist Open WebUI Integration

Memorist is a complementary local memory module for Open WebUI. Open WebUI remains the parent application and visible chat UI.

This bundle includes:

- a server-side Open WebUI Filter for fail-open preflight memory attachment;
- a small status Function;
- shared local-only client/config helpers;
- UI surface specifications for a future fork/add-on panel.

## Trust Warning

Open WebUI Filters and Functions execute Python code on the server. Install this bundle only from a trusted local Memorist release. Do not paste unreviewed Filter/Function code into Open WebUI.

## Local-only default

The integration accepts only local Memorist Core URLs such as `http://localhost:8777`, `http://memorist-core:8777`, or `http://host.docker.internal:8777`.
