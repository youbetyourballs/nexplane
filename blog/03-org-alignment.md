---
title: "Why security teams keep creating work for engineering"
date: 2026-05-16
category: technical
---

If you've worked in security long enough, you've been in this meeting. The CISO is presenting the open findings dashboard. Engineering leadership is in the room. The numbers are bad. Someone from security says the findings aren't getting resolved. Someone from engineering says they're buried in roadmap work. The meeting ends with a commitment to "prioritize security work" that everyone knows won't be honored.

Two months later, same meeting.

This isn't a cultural problem. It's a structural one. And the structure isn't going to fix itself.

## The root cause

Security teams have visibility without execution capability. A good security program can find every misconfigured S3 bucket, every stale IAM credential, every unpatched instance in the environment. Modern tooling makes that part relatively tractable.

What security teams typically cannot do is fix the problem they found. Not because they lack the knowledge, but because they lack the access, the tooling, and the organizational authority to make changes to infrastructure they don't own.

So they file a ticket. The ticket goes to engineering. Engineering has a sprint backlog that's already full with product work, technical debt, migration projects, and the last batch of security tickets. The new ticket goes to the bottom. The finding stays open. Security escalates. The relationship gets worse.

## Why the metrics make it worse

Security organizations are measured on open findings — time to detect, time to remediate, finding aging. These are the numbers that go to the board, to compliance auditors, to cyber insurance underwriters. A finding that's been open for 60 days is a liability.

Engineering organizations are measured on product velocity — features shipped, sprint completion, uptime. Security work consumes sprint capacity without advancing product goals. From an engineering team's perspective, every security ticket is an interruption to the work they're actually evaluated on.

These incentives are not aligned. They're directly in conflict. When security work lives in engineering's backlog, security's remediation metric degrades engineering's velocity metric. Engineering has no organizational incentive to prioritize it, and every organizational incentive to defer it.

You can try to fix this with governance. Make security tickets mandatory-priority. Define SLAs. Escalate to leadership. These interventions work temporarily and then the system reverts, because the underlying incentive structure hasn't changed.

## The false solution: "give security more access"

The obvious answer is to give security teams the access to fix things themselves. Add security engineers to the IAM admin group. Give them write access to infrastructure. Let them close their own findings.

This creates a different set of problems.

Unstructured infrastructure access is its own risk. When a security engineer makes a change directly in the AWS console or via a CLI script, there's no workflow, no change record, no approval gate, and no rollback. If the change breaks something — and changes to IAM, networking, and database configurations frequently break things in non-obvious ways — there's no clean way to undo it and no audit trail that engineering can trust.

Engineering's objection to this approach isn't unreasonable: "You want us to give security teams write access to production infrastructure with no process around it?" That's a legitimate concern. The answer isn't to argue security teams are trustworthy. The answer is to build the process.

## What the actual solution looks like

Security needs execution capability with constraints. Specifically:

**Typed actions, not raw access.** Instead of console access to IAM, security has the ability to create a "rotate IAM access keys" change request with a defined scope — specific users, a specific reason, a specific time window. The change request defines exactly what will happen before anything executes.

**Approval workflow.** The change request goes to whoever owns the affected resource — in this case, the IAM owner or the resource owner — for approval. Engineering isn't doing the work, but they're still in the loop for changes to infrastructure they care about. That's a workflow that engineering can trust.

**Rollback.** If the change breaks something, it can be undone in seconds. Not "re-run the playbook" — restore the exact before-state captured at execution time. This is the constraint that makes it safe to give security execution capability. The blast radius is bounded.

## A concrete example

The security team runs an IAM scan and finds 23 access key pairs that haven't been rotated in over 90 days. Under the old model: file a ticket, wait for engineering to schedule a sprint, wait for the sprint, wait for the fix, close the finding six weeks later.

Under a structured execution model: security creates a change request for key rotation across the 23 affected accounts, scoped to those specific keys. The change request shows exactly what will happen — old keys deactivated, new keys generated and stored in Secrets Manager, owners notified. The relevant IAM owner approves it. Security executes it. If a service breaks because it was using a hard-coded key that engineering didn't know about, the rollback restores the old key in seconds while the team investigates.

Engineering didn't have to touch it. The finding is closed. The audit trail shows who approved what and when. The IAM owner was in the loop. Nothing happened without oversight.

## The org alignment outcome

When security can close their own findings — through a workflow that engineering can audit and trust — the dynamic changes. Security isn't waiting on engineering. Engineering isn't being interrupted by security. The findings get remediated faster, the relationship is less adversarial, and the audit trail satisfies compliance requirements that previously required manual evidence collection.

The ticket handoff dysfunction isn't inevitable. It's the product of a capability gap. When you close the gap with structured execution rather than unstructured access, the structural problem goes away.
