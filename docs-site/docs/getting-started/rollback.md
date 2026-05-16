# Using Rollback

Every change request that completes successfully captures a **before-state snapshot** during execution. This snapshot contains enough information for Nexplane to reverse the change without requiring you to manually reconstruct the prior state.

## How rollback works

When you execute a CR, the connector's executor:

1. Reads the current state of the target resource and stores it as the snapshot.
2. Applies the change.
3. Writes the result and snapshot to the CR record.

When you trigger rollback:

1. Nexplane reads the snapshot from the CR record.
2. The executor runs the inverse operation using the snapshot data.
3. A new **Rollback CR** is created, linked to the original CR, with its own execution record and audit trail.

## Triggering a rollback

1. Navigate to **Change Requests** and open the completed CR you want to reverse.
2. Click **Rollback** in the action bar.
3. Add a reason for the rollback (optional but recommended for audit purposes).
4. Click **Confirm Rollback**.

The rollback CR moves through the same execution pipeline. By default, rollbacks do not require a separate approval step, but this can be configured per change type in **Settings → Change Types**.

## Rollback availability

| Condition | Rollback available? |
|---|---|
| CR status is `completed` | Yes |
| CR status is `failed` (partial execution) | Maybe — check snapshot field |
| CR has already been rolled back | No (one rollback per CR) |
| Connector credentials have changed since execution | Depends — credentials must still be valid for the inverse operation |
| Target resource has been deleted externally | No — rollback will fail; manual restoration required |

## Rollback limitations

- **Destructive actions** (e.g. deleting a resource) cannot be reversed if the resource no longer exists. Nexplane will surface this as a rollback failure.
- **Secret rotation** rollback restores the previous secret version. If the secret engine does not retain version history, rollback is not possible.
- See the [Rollback Failures runbook](../runbooks/rollback-failures.md) for troubleshooting steps.
