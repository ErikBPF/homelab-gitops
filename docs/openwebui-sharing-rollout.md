# OpenWebUI persisted sharing correction

**Status:** source/runbook prepared; production correction and acceptance pending.

`ENABLE_COMMUNITY_SHARING=false` supplies the default for
`ui.enable_community_sharing`; an existing database row takes precedence. The
rendered-environment contract proves only that default. It does not prove the
running service has disabled sharing. Keep global persistence enabled because
MCP tool registration uses the same database-backed configuration.

## Authorized rollout

Before this procedure, name the operator identity, `homelab` context,
`openwebui` namespace and maintenance window. Verify a restorable independent
backup of the existing 5Gi PVC. Keep the maintenance access restriction in place
until both stored-state and authenticated API checks below pass. This document
provides no live execution authority.

After the pinned v0.12.5 pod is ready, run this single-key correction through the
owning repository's operational workflow. The target names match the declared
Deployment; no credential value is supplied to the command or printed.

```bash
kubectl --context homelab -n openwebui exec -i deployment/openwebui -- python - <<'PY'
import asyncio

try:
    from open_webui.models.config import Config

    async def disable_sharing():
        await Config.upsert({"ui.enable_community_sharing": False})
        if await Config.get("ui.enable_community_sharing") is not False:
            raise RuntimeError()

    asyncio.run(disable_sharing())
except Exception:
    raise SystemExit("community sharing correction failed") from None
print("community_sharing_disabled=true")
PY
```

The operation is idempotent and updates only the sharing key. It preserves the
MCP registration and all other persisted configuration. A failure blocks rollout;
do not work around it by disabling global persistence or dumping configuration.

In the existing authenticated browser session, inspect the application's
`/api/config` response and verify only
`features.enable_community_sharing === false`. This field is absent from the
unauthenticated response, so its absence is not a passing check. Refresh the UI
and confirm the sharing control is unavailable. Do not copy authorization
headers, tokens or the full configuration response into evidence.

Record the source/image revision, stored-state check and authenticated boolean
result. Only then restore normal access. Repeat this correction and acceptance
after restoring an older PVC, since that backup can reintroduce the enabled row.
Recovery must preserve disabled sharing while using a compatible reviewed image
and verified state; the old vulnerable image is not a default rollback.

The command was syntax-checked locally, not executed against a running service.
No database row, PVC or deployment was changed during source preparation.

## Upstream basis

- [v0.12.5 configuration persistence](https://github.com/open-webui/open-webui/blob/v0.12.5/backend/open_webui/models/config.py): explicit upsert, stored-row precedence and default seeding.
- [v0.12.5 authenticated configuration response](https://github.com/open-webui/open-webui/blob/v0.12.5/backend/open_webui/main.py): the authenticated features object reads the same persisted key.
