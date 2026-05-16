# Runbook: Rollback Failures

Use this runbook when a rollback operation fails or the Rollback button is unavailable on a completed change request.

## Why rollback might not be available

| Condition | Explanation |
|---|---|
| CR status is `failed` and snapshot field is empty | Execution failed before the snapshot was captured — no before-state exists |
| CR has already been rolled back | Each CR can only be rolled back once |
| Change type does not support rollback | Some operations are inherently irreversible; the CR detail will indicate this |

## Common rollback failure causes

### 1. Snapshot not captured (execution failed partway through)

**Symptom**: The CR shows `failed` but the rollback button is greyed out or the rollback CR itself fails immediately with "No snapshot available."

**Explanation**: The before-state snapshot is captured at the start of execution, before the change is applied. If execution fails during the snapshot step (e.g. due to a permissions error reading the current state), no snapshot is stored and rollback is not possible.

**Resolution**: Manual restoration is required. Review the CR execution result for details on what was changed before the failure. Check the connector's audit log or the target system's own history to determine the resource's prior state.

---

### 2. Connector credentials changed since execution

**Symptom**: Rollback CR shows `failed` with `AuthenticationError` or `403 Forbidden`.

**Explanation**: Rollback uses the same connector account as the original execution. If credentials were rotated, revoked, or the service account was disabled between execution and rollback, the rollback executor cannot authenticate.

**Steps**:
1. Navigate to **Connectors → [Connector Type] → [Account Name] → Edit**.
2. Update the credentials to valid values.
3. Click **Test Connection** to verify.
4. Retry the rollback from the CR detail page.

See also: [Connector Credential Errors runbook](connector-credentials.md).

---

### 3. Target resource has been deleted or modified externally

**Symptom**: Rollback CR fails with `ResourceNotFound`, `NoSuchEntity`, or similar.

**Explanation**: The resource that was changed has been deleted or significantly modified by something outside of Nexplane since the original CR was executed. The rollback executor cannot apply the inverse operation to a resource that no longer exists in its expected form.

**Resolution**: Automated rollback is not possible. Manual restoration steps:

1. Open the CR detail page and expand the **Snapshot** section — this shows the resource's state at the time of execution.
2. Use the snapshot data to manually recreate or restore the resource in the target system.
3. Mark the CR as manually remediated by adding a note in the CR description (the system does not currently have a formal "manual remediation" status — use the description field for tracking).

---

### 4. Vault secret history truncated

**Symptom**: Vault `rotate_secret` rollback fails with `version not found` or `invalid version`.

**Explanation**: Vault's KV v2 engine only retains a configurable number of secret versions (`max_versions`). If the version captured in the snapshot has been deleted due to version limit, rollback cannot restore it.

**Resolution**: Check the current version history in Vault:

```bash
vault kv metadata get secret/<path>
```

If the target version is gone, restore the value manually from a backup or alternative source, then update the secret in Vault directly.

**Prevention**: Set `max_versions` to at least `5` on any secret path managed through Nexplane:

```bash
vault kv metadata put -max-versions=5 secret/<path>
```

---

## Escalation

If none of the above steps resolve the rollback failure, collect the following and escalate:

1. CR ID and rollback CR ID
2. Full error message from the rollback CR execution result
3. Backend logs from the time of the rollback attempt: `docker compose logs backend --since 1h`
4. The connector type and target system version
