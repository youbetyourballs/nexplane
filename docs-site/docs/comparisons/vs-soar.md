# Nexplane vs. SOAR

SOAR and Nexplane occupy adjacent but distinct layers of the security operations stack. They are designed to work together, not replace each other.

## What SOAR is

Security Orchestration, Automation, and Response (SOAR) platforms — examples include Splunk SOAR, Palo Alto XSOAR, and Tines — automate the workflow from alert detection to analyst response. SOAR tools are primarily designed to:

- **Respond to triggers** — Ingest alerts from SIEMs, EDR platforms, cloud security services, and other detections, then kick off automated playbooks.
- **Enrich and triage** — Fan out to threat intelligence APIs, CMDB, identity systems, and ticketing to gather context before a human sees the alert.
- **Notify and escalate** — Page on-call engineers, open tickets, send Slack messages, and coordinate multi-team response.
- **Manage cases** — Track the full incident lifecycle from detection through closure with a case record.

## Where SOAR falls short for remediation

SOAR actions are designed to be fast and automatic. This works well for enrichment and notification, but introduces risk when applied to changes that affect production systems:

- **Fire-and-forget execution** — SOAR actions run and move on. There is no concept of capturing the system state before the action executes.
- **No typed change schema** — SOAR playbook steps call APIs with ad-hoc parameters. There is no formal schema validation that the right account ID, the right resource, and the right scope are targeted before execution.
- **No approval gate** — SOAR is optimized for speed. Adding a human approval step before a remediation action is possible but awkward; it is not a first-class concept in most SOAR platforms.
- **No per-action rollback** — If a SOAR playbook disables the wrong user account or blocks the wrong IP, there is no built-in mechanism to undo exactly that action. Remediation requires manual intervention or writing a second playbook.
- **No change request lifecycle** — SOAR tracks incidents, not changes. The question "what was the state of this IAM policy before we modified it, and who approved the modification?" is not naturally answered by a SOAR case.

## Nexplane's role in the stack

Nexplane is not a replacement for SOAR incident triage and enrichment. It is the **execution layer for remediation actions** that require:

- Human approval before the change executes
- Typed, validated parameters (so the right resource is targeted)
- A before-state snapshot enabling rollback of exactly the executed operation
- An auditable record of who approved what, when, and what the system looked like before

SOAR handles detection, enrichment, and triage. Nexplane handles the controlled, reversible execution of the remediation action itself.

## Integration pattern

A typical integrated workflow:

1. **Detection** — A SIEM or cloud security service fires an alert (e.g., unusual IAM activity, suspicious login, exposed credential).
2. **Enrichment** — SOAR automatically enriches the alert: queries threat intel, looks up the affected user in the identity system, pulls recent activity logs.
3. **Triage and decision** — SOAR pages the analyst with enriched context. The analyst reviews and decides to act.
4. **CR creation** — SOAR calls the Nexplane API to create a change request (e.g., `disable_iam_user`, `revoke_role_binding`, `rotate_credential`). The CR is created in `pending_approval` state.
5. **Approval** — The analyst (or a second reviewer, depending on policy) approves the CR in Nexplane.
6. **Execution with rollback** — Nexplane executes the change, captures the before-state snapshot, and records the result. If the alert was a false positive, the analyst can roll back the change in seconds.

This pattern gives you SOAR's speed and breadth for triage combined with Nexplane's control and reversibility for execution.

## When SOAR alone is sufficient

- Enrichment and notification actions that are read-only (looking up data, sending alerts)
- Automated responses to high-confidence, low-risk detections where speed matters more than reversibility (e.g., auto-closing duplicate alerts)
- Organizations without compliance requirements around documented, approved changes

## When adding Nexplane makes sense

- Remediation actions that affect user access, credentials, or network policy — where a false positive could cause an outage
- Environments with change management requirements (CAB processes, SOC 2, PCI DSS) that require documented approval before changes execute
- Incidents where the analyst needs confidence that any action taken can be precisely undone if the situation changes
