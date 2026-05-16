# Nexplane vs. Ansible

Ansible and Nexplane solve different problems. Understanding where each fits prevents misuse of both tools.

## What Ansible is good at

Ansible is a mature, battle-tested configuration management and orchestration tool. It excels at:

- **Idempotent configuration management** — Run the same playbook repeatedly; Ansible converges hosts to the desired state without side effects.
- **Large-fleet operations** — Apply changes across hundreds or thousands of hosts in parallel using inventory groups and dynamic inventory plugins.
- **Provisioning and drift correction** — Bootstrap new instances, install packages, manage files and services, and detect when hosts drift from a known-good state.
- **Rich ecosystem** — Thousands of community modules covering cloud providers, network devices, databases, and security tools.

## Where Nexplane differs

Ansible was designed to answer "how do I change this system?" Nexplane answers "who approved this change, what was the state before, and how do I undo exactly this one operation?"

| Capability | Ansible | Nexplane |
|---|---|---|
| Approval workflow | None native (requires external tooling) | Built-in: `pending_approval` → `approved` → `executed` |
| Per-operation rollback | Re-run an earlier playbook version | Snapshot-based undo of exactly the operation that was executed |
| Before/after audit trail | Not captured per-run | Stored in every change request record |
| Change request lifecycle | None | Full CR lifecycle with timestamps, approver identity, and result |
| Typed change schema | Playbook variables (loosely typed) | Strongly typed parameters validated at submission time |

Ansible's rollback model is "apply a previous version of the playbook." This re-provisions rather than undoes. If you disabled an IAM user via Ansible and need to re-enable them, you change the playbook and re-run — but that re-runs the entire play, not just the one operation. Nexplane's rollback targets the exact operation that was recorded, using the before-state snapshot captured at execution time.

## Complementary, not competing

Nexplane includes an **Ansible connector**. You can create a change request that invokes a specific Ansible playbook against a specific inventory as its execution step. In this pattern:

- **Ansible** handles the "how" — the actual configuration logic lives in your playbooks.
- **Nexplane** handles the "who approved it, when, what was the state before, and how do we undo it" — the governance layer wraps the Ansible execution.

This means you don't have to choose between the two. Existing Ansible automation investments remain intact; Nexplane adds the approval gate and audit trail on top.

## When to use Ansible alone

- Bulk provisioning of new infrastructure where no approval gate is required
- Configuration drift correction at scale (scheduled, automated, low-risk)
- Operations where the full playbook is the unit of work and per-operation rollback is not needed
- Teams without compliance requirements around documented approvals

## When Nexplane adds value

- Security-sensitive changes (disabling accounts, revoking access, rotating credentials) that require documented approval before execution
- Operations where rollback must target exactly one change rather than re-running a full playbook
- Environments with audit requirements (SOC 2, ISO 27001, PCI DSS) where you need a timestamped record of who approved what and what the system state was before the change
- Changes initiated by an analyst in response to a security event, where speed and reversibility both matter
