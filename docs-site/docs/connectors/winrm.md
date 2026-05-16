# WinRM Connector

The WinRM connector uses `pywinrm` to execute PowerShell commands on Windows Server hosts over WS-Management (WinRM). Like the SSH connector, it enforces a strict allowlist — arbitrary PowerShell is never executed.

## Credential fields

| Field | Required | Description |
|---|---|---|
| `hostname` | Yes | Target Windows Server hostname or IP address |
| `port` | Yes | WinRM port — typically `5985` (HTTP) or `5986` (HTTPS) |
| `username` | Yes | Local or domain username (e.g. `DOMAIN\nexplane-svc` or `.\LocalAdmin`) |
| `password` | Yes | Account password |
| `use_ssl` | No | Set to `true` to use HTTPS (port 5986). Default: `false` |
| `verify_ssl` | No | Set to `false` to skip certificate verification. Default: `true` |

### WinRM setup on the target host

WinRM must be enabled and configured on the target host before Nexplane can connect. Run the following in an elevated PowerShell prompt:

```powershell
# Enable WinRM with HTTPS listener (recommended)
winrm quickconfig -transport:https

# Or for HTTP (lab/testing only)
winrm quickconfig
```

For domain-joined machines, WinRM can be configured via Group Policy.

## Command allowlist

The WinRM connector does not execute arbitrary PowerShell. All commands are defined as templates in the connector's allowlist. Permitted operations include:

- Local user account management (`Disable-LocalUser`, `Enable-LocalUser`)
- Service management (`Stop-Service`, `Start-Service`)
- Firewall rule modification (`Set-NetFirewallRule`)
- Windows Defender configuration

## Supported change types

### `disable_local_user`

Disables a local Windows user account using `Disable-LocalUser`.

| Parameter | Type | Description |
|---|---|---|
| `username` | string | Local account name to disable |

**Rollback**: Runs `Enable-LocalUser` to re-enable the account.

---

### `run_allowlisted_command`

Executes a specific allowlisted PowerShell command template on the remote Windows host.

| Parameter | Type | Description |
|---|---|---|
| `command` | enum | Allowlisted command key |
| `args` | object | Named arguments for the command template |

**Rollback**: Executes the registered inverse command if defined.

!!! warning "SSL strongly recommended"
    WinRM over HTTP transmits credentials in cleartext (with NTLM/Kerberos negotiation, but the channel itself is unencrypted). Always use `use_ssl: true` with a valid certificate in production environments.
