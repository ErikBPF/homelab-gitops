# Aster Authentik reader handoff

This change stages an Aster-only ExternalSecret and `vault-aster-authentik-reader` ClusterSecretStore. Its OpenBao AppRole can read only `secret/data/platform/aster/authentik-reader`, and the store is available only to the `aster` namespace. The server deployment does not reference the new Kubernetes Secret or enable shared-model authority or use.

Before any deployment or authority enablement, an authorized operator must:

1. Bootstrap the read-only Authentik service account, role, and group through the `homelab-iac` `aster-identity-reader` unit using an approved admin identity. The standing Authentik config-manager cannot create users or groups.
2. Issue a token for that dedicated account through the sanctioned private credential handoff. Publish only `authentik_read_token` to OpenBao `secret/platform/aster/authentik-reader` through the `homelab-iac` `aster-authentik-reader` secret unit. Do not put the token in Terraform state, Git, logs, or chat.
3. Seed the `external-secrets` namespace Secret `vault-approle-aster-authentik-reader` with the dedicated consumer AppRole `role_id` and `secret_id` using the approved private bootstrap procedure. Neither value is present in this repository. Confirm the ClusterSecretStore and ExternalSecret are Ready without printing Secret data.
4. Verify the Aster OIDC user UUID claim and configured admin/editor group UUIDs against the live Authentik provider. Prove that the first request after a group removal is denied, including an existing session and saved model choice, and that IdP lookup failure denies shared use. Only then review activation of `ASTER_SHARED_MODEL_AUTHORITY_ENABLED` and `ASTER_SHARED_MODEL_USE_ENABLED` in the owning Aster deployment configuration.

The Aster directory has no Argo CD Application under `clusters/kepler/apps`, so this ExternalSecret is not currently included in a sync. Aster's current NetworkPolicy only permits TCP 443 egress to in-cluster pods, while its configured OIDC issuer uses external `https://auth.pastelariadev.com`; the Authentik API origin and a narrow reachable egress path must be verified before authority can work. Do not add an unrestricted 443 CIDR rule. The manifest is source-only until these dependencies are satisfied. Its presence does not prove a working token, OpenBao read, or live revocation behavior.
