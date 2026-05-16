---
title: "Why we built Nexplane"
date: 2026-05-16
category: founder
---

I've had a version of the same conversation more times than I can count. It goes like this: a security engineer, usually someone sharp and frustrated, tells me about a finding that's been open for six weeks. Critical severity. Clear remediation path. The fix takes maybe two hours of engineering time.

It's been open for six weeks because it's in an engineering sprint backlog, competing with feature work, technical debt, and twelve other things the team was promised they'd get to this quarter. The security engineer has escalated twice. The relationship with engineering is getting worse. And the finding is still open.

That's the problem we built Nexplane to solve.

## What we thought the solution was

The first version of our thinking was simple: security teams need execution capability. If they can find problems but can't fix them, give them the ability to fix them. Straightforward.

The safety constraint turned out to be the whole problem.

"Give security more access" is not a solution that engineering teams accept, and they're not wrong to resist it. Unstructured access to production infrastructure — IAM, networking, databases, cloud resources — is genuinely dangerous. Not because security engineers are untrustworthy, but because infrastructure is complex, changes have non-obvious dependencies, and without a workflow, there's no coordination, no audit trail, and no way to undo something that breaks unexpectedly.

We talked to engineering leaders who'd tried it. A security team gets elevated access to fix their own findings. They make a change to an IAM policy that unexpectedly breaks a service integration. Nobody knew the dependency existed. Now engineering is dealing with an outage caused by a change they didn't approve, didn't know about, and can't easily trace.

That experience poisons the well. Engineering stops trusting security's judgment. Security stops getting access. The ticket queue gets longer.

So unstructured access is dangerous. But no access means nothing gets fixed. We had to find what was between those two options.

## The design insight

The answer came from thinking about what made the situation feel unsafe versus safe.

The unsafe version: a security engineer with console access making ad hoc changes to resources they found in a scan. No record of what they did before the change. No approval from the resource owner. No way to undo it cleanly if something breaks.

The safe version: a security engineer creates a change request that describes exactly what will happen — which resources, what operation, what the expected outcome is. The resource owner reviews and approves it. The change executes. If something breaks, the before-state was captured and the change can be rolled back in seconds.

Those two scenarios have the same security engineer making the same change to fix the same finding. The difference is structure. Approval. Rollback.

That's what Nexplane is: typed change requests with approval workflow and per-operation rollback.

Not a shell script. Not Terraform. Not an LLM that calls AWS APIs directly. A change request that any stakeholder can read, that requires a human decision before anything runs, and that can be undone completely in seconds if something goes wrong.

## Why rollback is the key constraint

We made a lot of design decisions building this. The one that mattered most was treating rollback as a hard requirement from the beginning, not something we'd add later.

Here's why: rollback is what makes it safe to give security execution capability in the first place. If a change can't be undone, the blast radius of a mistake is unbounded. Engineering is right to resist that. But if every change can be rolled back in seconds — not re-run-the-playbook rolled back, but restore-the-exact-before-state rolled back — the risk calculus changes.

The honest version of "we can roll this back" is: we captured the complete before-state at the moment the change executed, we stored it with the change record, and we have a typed undo operation that restores exactly that state. That's not the same as "we have audit logs we can reconstruct from" or "we can re-apply the previous Terraform configuration." Those are forensics. This is undo.

Making that real required every connector to implement before-state capture as a first-class concern. It's the thing we've been most disciplined about. If a connector can't capture before-state, it doesn't get a rollback operation, and we're explicit about that limitation.

## Why we built the AI piece the way we did

We added an AI assistant relatively early because the alternative — requiring security engineers to manually compose typed change requests — was too slow. When you're looking at a scanner output with 400 findings, you need to be able to say "rotate the keys for all IAM users who haven't rotated in 90 days" and have the system understand that and build the right change requests.

But we built the AI piece as a strict interpreter, not a free-form executor. The LLM's job is to translate natural language into a typed action with a fixed schema. It generates `rotate_iam_access_keys` with specific user names and a reason. It does not call AWS APIs directly. It does not decide what "rotate" means at execution time. The typed action goes through validation, then approval, then a constrained executor that knows exactly what operations to perform.

This distinction matters because the failure modes of a free-form LLM executor are bad in the specific ways that security operations can't afford: ambiguous intent, hallucinated parameters, irreversible actions taken with false confidence. We hit all of those failure modes in early testing. The typed action architecture is what fixed them.

## Where we are and what we know is missing

We have connectors for the major cloud providers, identity systems, databases, and security tools. The connectors that exist are production-ready — we use the rollback guarantee as the quality bar. If it can't roll back cleanly, it doesn't ship.

We're in early access. We're working with security teams who are willing to push on the edges of what the system can do, and we're building the connector coverage they need. Some of the things we know we need — more database connector breadth, better handling of chained changes that touch multiple systems, tighter integration with specific SIEM platforms — are on the near-term roadmap.

The finding that's been open for six weeks because it's stuck in an engineering backlog: that's the problem we're solving. Not by taking engineering out of the loop, but by building the workflow that lets security act without creating work for engineering — and that gives everyone involved the audit trail, the approval record, and the rollback capability to trust that the work was done right.

That's why we built this.
