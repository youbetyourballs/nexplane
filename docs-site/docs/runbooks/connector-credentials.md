# Runbook: Connector Credential Errors

Use this runbook when a change request fails with a credential or authentication error against a connector.

## Symptoms

- CR status shows `failed` with an error like `AuthenticationError`, `InvalidClientTokenId`, `401 Unauthorized`, or `403 Forbidden`
- Test Connection fails when editing a connector account
- Rollback fails with a credential error even though execution previously succeeded

## Common causes and fixes

### Expired or rotated credentials

Credentials in the target system may have been rotated outside of Nexplane (e.g. an AWS access key was manually rotated, or a Vault token expired).

**Fix**: Update the connector account with the new credentials.

1. Navigate to **Connectors → [Connector Type] → [Account Name] → Edit**.
2. Enter the new credential values.
3. Click **Test Connection** to verify.
4. Click **Save**.

!!! warning
    If credentials were rotated after a CR was executed but before rollback was attempted, the rollback will fail because it uses the same connector account. Update credentials before attempting rollback.

### Insufficient permissions

The Nexplane service account may have had permissions revoked since it was set up.

**Fix**: Review the minimum required permissions documented on the connector's page and restore the missing permissions in the target system.

### Account disabled or locked

The service account Nexplane uses may have been disabled in the target system (e.g. the IAM user was disabled, the Vault token was revoked, the AD service account was locked out).

**Fix**: Re-enable or unlock the service account in the target system, then re-test the connector.

### Network or firewall change

A firewall rule change may be blocking Nexplane's outbound connections to the target API.

**Fix**: Verify that the control plane host can reach the target API endpoint:

```bash
# Example: test AWS STS connectivity
curl -v https://sts.amazonaws.com/

# Example: test Vault
curl -v https://<vault-host>:8200/v1/sys/health
```

### Wrong region or endpoint

For connectors with region-specific endpoints (AWS, Azure), the configured region or endpoint may not match where the resources exist.

**Fix**: Update the `region` field on the connector account to match the target resource's region.

## Still failing after credential update?

1. Check the backend logs for the full error traceback: `docker compose logs backend --tail 200`
2. Look for the `connector_account_id` in the log line to confirm which account was used.
3. If the error is `SSLError` or certificate-related, see the SSL troubleshooting notes on the relevant connector page.
