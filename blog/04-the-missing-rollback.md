---
title: "The rollback nobody built"
date: 2026-05-16
category: technical
---

Every infrastructure and security tool has some notion of rollback. None of them give you per-operation undo of exactly what just happened.

This sounds like a minor gap until you're in an incident at 2 AM and someone asks "can we undo the change we made 15 minutes ago?" The answer, in most tooling stacks, is some version of "it's complicated."

Here's why.

## Terraform

Terraform is the canonical example of infrastructure as code done right. State is tracked. Diffs are previewed. Changes are version-controlled. "Rollback" in Terraform means reverting to a previous version of your configuration and applying it. Terraform will figure out what needs to change to get the environment back to that state.

That works well for additive changes — resources that were created can be destroyed, resources that were modified can be reverted. But there are two categories where it breaks down.

First: operational changes. Disabling an IAM user, revoking a role binding, rotating a credential, blocking a security group rule — these are changes to the operational state of existing resources, not to the resources themselves. Terraform doesn't model these. A user's enabled/disabled status isn't typically in your IaC. If you disable an IAM user outside of Terraform (or even through a Terraform change and then something else changes it), Terraform has no reliable rollback story.

Second: the re-provisioning problem. "Apply a previous configuration version" doesn't restore what existed before — it re-provisions toward a desired state. If your database had specific connection parameters, your load balancer had specific listener rules, your IAM role had specific inline policies — Terraform's rollback means re-applying a config that may not perfectly reconstruct what was there. The previous state is gone; you're building something new that resembles it.

## Ansible

Ansible's model is idempotent desired state: you define what things should look like, and Ansible makes them look that way. There is no native rollback concept. You can write playbooks that undo a previous playbook's work, but that's a separate artifact you have to maintain — and it has to be written and tested before you need it.

Re-running a playbook applies desired state forward. It doesn't restore a snapshot. If your playbook modified a configuration file and the file has been edited by something else since then, re-running the playbook applies the playbook's version of the file, not the version from before your last run. The before-state is simply not preserved.

This isn't a criticism of Ansible. It was designed for configuration management, not change management. The absence of rollback is a design choice appropriate to its purpose.

## SOAR

Security orchestration platforms are fire-and-forget by design. A SOAR playbook isolates an endpoint, blocks an IP, disables a user account, quarantines a file. These are response actions — the point is speed and automation, not reversibility.

The assumption built into most SOAR architectures is that response actions are intentional and correct. If you isolated the wrong endpoint, the remediation path is another action: un-isolate it. But that un-isolation action doesn't restore the before-state of the endpoint — it creates a new state that resembles the original. And it requires knowing that the wrong thing happened, diagnosing what the before-state was, and manually constructing the corrective action.

There is no rollback model in SOAR because SOAR was designed for a world where response actions are assumed good and the priority is execution speed.

## CloudTrail and audit logs

This is the one people reach for when rollback isn't available: "we have CloudTrail, we can reconstruct what happened." CloudTrail is excellent. It records API calls, resource changes, and access events with high fidelity. It's the right tool for forensics and compliance.

But CloudTrail tells you what happened. It doesn't give you a one-click undo.

Reconstructing a rollback from CloudTrail logs means: identify the relevant API calls, infer the before-state from the changes recorded, manually construct and execute API calls to restore that state. For a simple change on a single resource, this is a 10-minute exercise. For a complex change that touched 40 IAM policies and 12 service accounts, it's a multi-hour investigation that's happening while an outage is ongoing.

CloudTrail is forensics infrastructure. It was never designed to be rollback infrastructure.

## What per-operation rollback actually requires

The gap across all of these tools comes down to the same thing: none of them were designed to capture before-state at the moment of execution and bind it to the specific operation that caused the change.

Per-operation rollback requires:

**Before-state capture at execution time.** Not "what did our IaC config say before the PR", not "what does CloudTrail record about the change" — what was the actual state of the specific resources being modified, captured immediately before the change executes. For a credential rotation, that means the exact credential configuration, expiration, and attached permissions. For a firewall rule modification, that means the exact rule set.

**Storage bound to the change record.** The before-state has to be stored alongside the change that caused it. Not in a separate log, not reconstructable from audit data — directly associated with the change record so that any operator can find it.

**A typed undo operation.** Rolling back isn't "apply the before-state as a new configuration." It's running a specific undo operation that knows how to restore this type of change. Rotating credentials back requires different steps than re-enabling a user account. The undo operation has to be as typed and as constrained as the original operation.

## Why this matters specifically for security operations

Security operations have a specific pattern that makes rollback critical: high-stakes changes executed quickly under uncertainty.

When you disable a user account because you suspect credential compromise, you need to be right — but you also need to be able to undo it quickly if you're wrong. Security teams make this call multiple times a week. The on-call engineer gets a Slack message: "we disabled alex.chen's account 10 minutes ago, but we just confirmed it's a false positive, can you restore it?" 

With before-state capture, this is a one-click operation. Without it, it's a manual reconstruction: what policies did the user have? what groups were they in? what was their console access status? what keys were active? Getting all of that right from memory or audit logs, under pressure, at 2 AM, is where mistakes happen.

The invariant is simple: rollback is only as good as the snapshot. If the executor didn't capture before-state at the moment of execution, you don't have a rollback — you have a forensics exercise. These are not the same thing.

The rollback gap isn't an oversight in any of the tools above. Each of them was designed for a specific purpose, and rollback wasn't the constraint. But security change management is the domain where that constraint matters most, and it's the capability that's been missing.
