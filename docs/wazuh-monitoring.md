# Wazuh endpoint monitoring

The `wazuh-orion-agent-unavailable` Grafana rule monitors the only currently
configured endpoint agent. It fires after five minutes when Orion's
`podman-wazuh-agent.service` is inactive or its active-state metric disappears.
It uses existing host telemetry and the critical Discord notification route.
An active process is not proof that the manager receives or indexes events.

When it fires:

1. Check Orion's agent and `wazuh-agent-vault` dependency with the Desktop
   repository's service diagnostics. Do not print environment files or keys.
2. Check the manager roster using `kubectl --context homelab -n wazuh exec
   wazuh-manager-master-0 -- /var/ossec/bin/agent_control -l`.
3. Repair through `desktop-nixos`, then verify a fresh host event reaches the
   manager and alert index. Verify notification delivery separately.
4. If the metric is missing, inspect Orion's Alloy collector and Prometheus
   ingestion before concluding that the agent stopped.

Do not suppress the rule just because the dashboard responds. Its TCP health
checks cannot detect absent endpoint telemetry. Other fleet nodes are not
covered by this rule; extend the expected agent roster as they are enrolled.
UniFi raw-event ingestion and decoder coverage require their own evidence.

Validate rule behavior with `tests/monitoring-promql.py` and rendering with
`tests/monitoring-contract.sh`. Publish the reviewed revision before using
`just sync <full-commit-sha> monitoring`; verify the Application and Grafana
rule state after sync. Reverting the rule requires adding its UID to
`deleteRules`, because Grafana does not prune deleted file-provisioned rules.

Enrollment trust ownership and the S06/S07 boundary are recorded in
[the Wazuh enrollment trust receipt](../platform/wazuh/ENROLLMENT-TRUST.md).
