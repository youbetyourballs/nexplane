# Connecting Your First Account

An **account** is a set of credentials tied to one connector type (e.g. your production AWS account or your Vault cluster). Nexplane stores credentials encrypted at rest and never exposes them after initial entry.

## Add an account

1. In the sidebar, navigate to **Connectors**.
2. Select the connector type you want to add (e.g. **AWS**).
3. Click **Add Account**.
4. Fill in the credential fields. Each connector type has different required fields — see the [Connectors](../connectors/index.md) section for field references.
5. Give the account a descriptive name (e.g. `prod-aws-us-east-1`).
6. Click **Test Connection**. Nexplane will validate credentials by making a lightweight read-only API call to the target system.
7. Click **Save** if the test passes.

## Credential security

- Credentials are encrypted with AES-256 using the `ENCRYPTION_KEY` from your environment before being written to the database.
- The plaintext value is never logged and is only decrypted in memory at the moment a change is executed.
- If you rotate credentials in the target system, update the account in Nexplane immediately — stale credentials will cause execution failures.

## Account permissions

Each connector type documents the minimum required permissions for its service account. As a rule, Nexplane needs:

- **Read** access to capture before-state snapshots for rollback
- **Write** access only for the specific operations you intend to use

Avoid granting administrator-level access unless required by a specific change type.

## Next step

[Create your first change request](first-change-request.md)
