# HTTPS callbacks, diagnostics and scanning

S5 preserves SWAG's existing public TLS and redirect ownership. OpenWebUI ingress TLS stays disabled because SWAG terminates the browser connection.

## Ordered HTTPS rollout

1. Adopt Authentik provider3 through the owning IaC OpenWebUI unit. Add the strict HTTPS callback while retaining the HTTP compatibility callback. Preserve client identity, secret, application and bindings. Stop if the reviewed plan changes anything else.
2. After IdP HTTPS registration is observed, deploy the OpenWebUI HTTPS WEBUI_URL and OPENID_REDIRECT_URI values. Verify public redirect, certificate, callback and an authenticated login. Inspect effective persistent `webui.url`; if it overrides environment configuration, back up the database and change only that key through the owner procedure. Preserve sharing=false and other configuration.
3. Once login and application URL are proven, remove the compatibility HTTP callback in IaC and verify login again. Until then the migration is incomplete.

If login fails, retain both IdP callbacks and revert the consumer values to the previous working revision. Keep a verified recovery session; do not rotate the client secret as part of URL alignment. No live IdP or frontend URL rollout occurred without the existing config-manager credential.

MCP echo redacts credential-shaped header names in HTTP JSON and MCP JSON/SSE results. Query strings are omitted from request logs; bodies remain omitted. Harmless identity headers and repeated header order are preserved. This is a diagnostic allow-through service: do not send secrets under arbitrary non-credential names. Verify with `bash tests/mcp-echo-contract.sh`.

## S7 scanner

The Security workflow runs checksummed Gitleaks8.24.3 over checked-out HEAD ancestry on main pushes, pull requests and manual dispatch. Read-only checkout does not retain credentials. PRs never enter the security webhook step. Trusted failures send a workflow link with findings withheld. A missing webhook does not mask scanner failure.

`python3 tests/security-workflow-contract.py` executes synthetic clean, leaked and removed-history cases with the actual scanner, plus stubbed notification transport. It runs in the Security job where the pinned scanner is installed; general manifest validation needs no scanner installation.

Publish and observe a successful `secrets` job on main before applying the IaC required-check consumer. Preserve the independent review ruleset. Reverting enforcement requires an owner-reviewed IaC change, never bypassing a failed scanner to merge.

Scanner policy is fixed default rules plus the immutable PR base commit's exact-fingerprint list (reviewed checked-out main policy on trusted push/dispatch). The CI workspace replaces its automatically loaded ignore file with that trusted snapshot; explicit ignore-path alone is insufficient in Gitleaks8.24.3. Inline gitleaks:allow comments cannot suppress results. Regression fixtures commit malicious config, ignore fingerprints and inline comments and still require failure. Policy changes take effect only after review and landing, before dependent content. Run the Security workflow for equivalent trust-boundary verification; an unqualified local command may load local policy.
