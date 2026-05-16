# SSH Connector

The SSH connector uses `paramiko` to connect to Linux hosts over SSH and execute a restricted set of allowlisted commands. It is also used to install and configure the Nexplane agent on remote hosts.

## Credential fields

| Field | Required | Description |
|---|---|---|
| `hostname` | Yes | Target host IP address or FQDN |
| `port` | Yes | SSH port (default: `22`) |
| `username` | Yes | SSH username on the target host |
| `private_key` | No | PEM-encoded private key (preferred) |
| `password` | No | Password authentication — use only if key-based auth is unavailable |
| `host_key_verification` | No | Set to `false` to skip host key checking (not recommended for production). Default: `true` |

Either `private_key` or `password` must be provided.

## Command allowlist

The SSH connector does not execute arbitrary commands. Only operations defined in the connector's allowlist are permitted. The allowlist covers:

- User account management (`usermod`, `passwd`, `chage`)
- Service control (`systemctl start/stop/restart/status`)
- File permission changes (`chmod`, `chown` on specific paths)
- Agent installation and registration scripts

Any command outside the allowlist is rejected before the SSH session is opened.

## Supported change types

### `lock_linux_user`

Locks a local Linux user account using `usermod -L`, preventing password-based authentication.

| Parameter | Type | Description |
|---|---|---|
| `username` | string | Local username to lock |

**Rollback**: Runs `usermod -U` to unlock the account.

---

### `run_allowlisted_command`

Executes a specific allowlisted command on the remote host.

| Parameter | Type | Description |
|---|---|---|
| `command` | enum | Allowlisted command key (see system settings for available values) |
| `args` | object | Named arguments for the command template |

**Rollback**: Executes the registered inverse command if one is defined for the selected command key.

## Agent installation via SSH

When installing the Nexplane agent on a remote Linux host, the SSH connector is used to:

1. Copy the agent binary to the target host
2. Create a systemd unit file
3. Configure the agent with the control plane URL and registration token
4. Enable and start the agent service

See the [Architecture](../architecture.md) page for details on the agent's role.
