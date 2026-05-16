# Your First Change Request

A **Change Request (CR)** is a proposal to execute a specific change against a connected account. It moves through a review and approval process before anything touches production.

## Step-by-step walkthrough

### 1. Open the Change Requests tab

Click **Change Requests** in the top navigation bar. You will see the CR list, which is empty on a fresh installation.

### 2. Create a new CR

Click **New Change Request** in the top-right corner.

### 3. Select a change type

Use the **Change Type** dropdown to choose the operation you want to perform (e.g. `disable_iam_user`). The list is filtered to change types supported by the connectors you have configured.

!!! tip
    If you have the AI assistant enabled, you can describe what you want to do in plain language in the prompt box and the assistant will suggest the appropriate change type and fill in parameters.

### 4. Select the target account

Choose the connector account to target (e.g. `prod-aws-us-east-1`).

### 5. Fill in parameters

Each change type has a set of typed input fields. For example, `disable_iam_user` requires:

- `username` — the IAM username to disable

Fill in all required fields. Optional fields will be shown with a grey label.

### 6. Add context (optional but recommended)

Fill in the **Title** and **Description** fields to explain why this change is needed. This context is stored with the CR and appears in the audit log.

### 7. Submit for approval

Click **Submit for Approval**. The CR status changes to `pending_approval` and any configured approvers are notified.

### 8. Approve the CR

If you have approval rights (or are the admin in a single-user setup), navigate to the CR detail page and click **Approve**. Add an approval note if desired.

### 9. Execute

Once approved, click **Execute**. Nexplane calls the connector with the provided parameters. Execution typically completes in 2–15 seconds depending on the target system.

The CR status will transition to `executing` and then to `completed` (or `failed` if an error occurs).

### 10. Review the result

The CR detail page shows:

- **Execution result** — success or failure with error details
- **Snapshot** — the before-state captured for rollback
- **Executed at** — timestamp and executing user

### 11. Optionally roll back

If the change needs to be reversed, click **Rollback** on the CR detail page. See [Using Rollback](rollback.md) for details.
