# Runbook: Agent Registration Failure

Use this runbook when a Nexplane agent fails to register with the control plane or appears offline in the UI.

## Symptoms

- Agent host shows **Offline** or **Never connected** in Settings → Agents
- Agent logs show `Registration failed` or `Connection refused`
- Change requests targeting the agent time out during execution

## Diagnostic steps

### 1. Check agent service status on the host

SSH to the agent host and check the systemd unit:

```bash
systemctl status nexplane-agent
journalctl -u nexplane-agent -n 100
```

Look for error messages in the logs. Common errors and their meanings are listed below.

### 2. Verify connectivity to the control plane

From the agent host, test that the control plane URL is reachable:

```bash
curl -v https://<control-plane-host>/api/health/
```

Expected response: HTTP 200 with `{"status": "ok"}`.

If this fails:
- Check firewall rules — the agent needs **outbound** HTTPS (port 443) to the control plane host.
- Verify DNS resolution: `nslookup <control-plane-host>`
- Check that the control plane is running: `docker compose ps` on the control plane host.

### 3. Verify the registration token

The agent is configured with a registration token that the control plane issues. If the token has expired or been revoked:

1. In the Nexplane UI, go to **Settings → Agents**.
2. Click **Generate New Token** for the agent.
3. On the agent host, update `/etc/nexplane/agent.conf` with the new token:

```ini
[nexplane]
control_plane_url = https://<your-control-plane>
registration_token = <new-token>
```

4. Restart the agent: `systemctl restart nexplane-agent`

### 4. Check TLS certificate issues

If the control plane uses a self-signed or internal CA certificate, the agent will reject it by default. Options:

- Add the CA certificate to the agent host's system trust store
- Set `verify_ssl = false` in `agent.conf` (not recommended for production)

### 5. Check for duplicate agent names

Each agent must have a unique name. If two agents share a name, the second to register will be rejected. Check the name in `agent.conf` and ensure it is unique across all agents.

## Resolution

After applying a fix, restart the agent and check the UI within 30 seconds — the agent polls the control plane on startup and then every 30 seconds.

```bash
systemctl restart nexplane-agent
journalctl -u nexplane-agent -f
```

A successful registration looks like:

```
INFO  Agent registered successfully. Agent ID: a1b2c3d4
INFO  Polling for tasks...
```
