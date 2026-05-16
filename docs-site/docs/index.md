# Nexplane

**Nexplane** is a security change management platform that gives security teams a controlled, auditable, and reversible way to execute infrastructure changes across cloud providers, identity systems, secrets managers, databases, and more.

## Why Nexplane

- **Rollback guarantee** — Every executed change captures a before-state snapshot. If something goes wrong, a single click (or API call) restores the system to its prior state. No manual undos, no guesswork.
- **AI-assisted safety** — Describe what you need in plain language and Nexplane's AI assistant translates it into a typed, structured change action with explicit parameters. You review what will happen before anything touches production.
- **Org alignment** — Changes flow through an approval workflow so the right people sign off before execution. Every action is attributed, logged, and auditable.

## How it works

1. An operator or AI assistant creates a **Change Request (CR)** describing the action to take (e.g. disable an IAM user, rotate a Vault secret).
2. The CR is submitted for **approval** by the designated reviewer.
3. Once approved, the CR is **executed** against the target system via the appropriate connector.
4. The result — including a rollback snapshot — is stored. **Rollback** is available immediately.

## Get started

Ready to run Nexplane? Head to the [Quick Start guide](getting-started/quickstart.md) to get a local instance running in under five minutes with Docker Compose.
