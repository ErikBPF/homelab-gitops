# Aster Authentik reader: implementation one-pager

## Outcome and purpose

This implementation stages narrowly scoped secret delivery for Aster's future
Authentik identity reader. It does not activate shared-model authority, enable
shared-model use, or deploy Aster. The goal is to make the identity-reader
boundary reviewable before introducing credentials or enabling authorization.

The existing [operator handoff](aster-authentik-reader.md) remains the activation
contract. This change preserves its separation between source configuration,
credential bootstrap, deployment, and proof of live authorization behavior.

## Delivered changes

- A dedicated `vault-aster-authentik-reader` ClusterSecretStore uses the existing
  OpenBao endpoint, `http://100.103.52.100:8200`. Its complete namespace condition
  admits only `aster`; other stores retain their current configuration.
- The store references a dedicated AppRole credential Secret in the
  `external-secrets` namespace. No token, role ID, or SecretID value is introduced
  by this change.
- An Aster ExternalSecret maps only `authentik_read_token` from
  `platform/aster/authentik-reader` into `aster-authentik-reader-secret` in the
  `aster` namespace.
- The reader contract is included in the existing validation runner. It checks
  endpoint consistency, the entire namespace-condition structure, credential
  references, the single-field mapping, and absence of authority/use activation
  from the server deployment.

Review identified two defects in the original staging change: the new store
retained Discovery's retired address, and its test inspected only the first
namespace regex. The implementation corrects that endpoint and replaces the
partial assertion with complete conditions equality. It adds no dependency or
cross-repository build-time read.

## Verification and limits

Test-first evidence records an endpoint comparison failing against the retired
address and passing after correction. A separate assertion failed when the old
contract accepted unauthorized namespace broadening. With the stronger contract,
all four private mutation cases are rejected: an extra regex, another condition,
an explicit namespace list, and a namespace selector. The valid manifest passes.

Focused reader and lane contracts, Bash syntax, and whitespace checks passed.
The complete owner recipe, `just test`, passed in 160.2 seconds. Recorded schema
summaries contain 492 valid resources, zero invalid resources or errors, and 170
skipped under the existing missing-schema policy. Independent implementation
review found no remaining issues. The tested implementation source was unchanged
through final reconciliation; this companion document was added afterward.

These are local static results, not proof of API-server admission, Harbor or
OpenBao availability, credential validity, or live authorization and revocation.

## Dependencies, rollout, and recovery

The companion `homelab-iac` identity, permission, and AppRole changes are separate
work. An authorized operator must bootstrap them, issue the dedicated read-only
token through the private handoff, publish its sanctioned OpenBao field, and seed
the consumer AppRole credentials. This PR performs none of those operations.

Aster has no corresponding Argo CD Application, and its external Authentik egress
path still needs narrow verification. Before activation, verify UUID claims and
group identities, deny the first request after group removal even with an old
session or saved model selection, and deny shared use when identity lookup fails.

Keep authority/use flags disabled until those gates pass. Source recovery is a
reviewed Git revert; it does not revoke issued credentials or undo runtime secret
state. Credential revocation and any manual synchronization require separate,
explicit authorization.
