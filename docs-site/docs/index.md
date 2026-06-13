# Nexplane

**Nexplane** is the control plane for security execution. It gives security teams a controlled, auditable, and reversible way to execute infrastructure changes across cloud providers, identity systems, secrets managers, databases, EDR, IaC, and more — without routing every change through sysadmin, network, or SRE queues.

## Why Nexplane

- **Rollback guarantee** — Every executed change captures a before-state snapshot. If something goes wrong, a single click (or API call) restores the system to its prior state. No manual undos, no guesswork.
- **AI-assisted safety** — Describe what you need in plain language and Nexplane's AI assistant (Anthropic Claude or OpenAI) translates it into a typed, structured change action with explicit parameters. You review what will happen before anything touches production.
- **Org alignment** — Changes flow through an approval workflow so the right people sign off before execution. Every action is attributed, logged, and auditable.
- **Authentication & SSO** — local JWT auth or OIDC single sign-on, switchable per organization.

## What's in the platform

- **Change Requests** — safety-reviewed, approval-gated, audited, with automatic rollback
- **Projects & Runbooks** — sequence related changes with dependency tracking; chain change types into reusable multi-step workflows with branching and human checkpoints
- **Incident Response** — fast-path playbooks for host isolation, account lockdown, evidence preservation, and phishing response
- **Vulnerability Remediation** — close the loop between scanner findings and automated fixes, with SLA tiers and CVE blast-radius
- **Compliance & Governance** — CIS benchmark enforcement, drift detection, change-freeze windows, audit evidence
- **Security Policy Auto-Generation** — synthesize least-privilege seccomp/AppArmor/SELinux/eBPF policies from observed behavior
- **Nexplane Agent** — a cross-platform Go binary (Linux, Windows, macOS) that runs on managed hosts and executes signed commands over outbound HTTPS — no inbound SSH required
- **70+ connectors** spanning cloud, identity, EDR, IaC, ticketing, and observability

## How it works

1. An operator or AI assistant creates a **Change Request (CR)** describing the action to take (e.g. disable an IAM user, rotate a Vault secret).
2. The CR is submitted for **approval** by the designated reviewer.
3. Once approved, the CR is **executed** against the target system via the appropriate connector.
4. The result — including a rollback snapshot — is stored. **Rollback** is available immediately.

## Get started

Ready to run Nexplane? Head to the [Quick Start guide](getting-started/quickstart.md) to get a local instance running in under five minutes with Docker Compose.
