---
title: "We gave an AI agent infrastructure access. Here's what we learned."
date: 2026-05-16
category: technical
---

There's a version of "AI for infrastructure" that sounds simple: user types a request, LLM interprets it, API call executes, done. We built that version first. Here's what broke.

## The naive implementation

The setup was straightforward. A user could type something like "disable the AWS account for john.doe, he's been offboarded." The LLM would parse the request, construct the appropriate AWS API call, and execute it. Fast, flexible, no YAML, no playbook.

The first category of failure was ambiguous intent. "Disable john.doe's account" could mean deactivate the IAM user, delete the access keys, revoke console access, or some combination. The LLM would pick one interpretation — confidently — and proceed. Sometimes it was right. Sometimes it disabled the wrong resource. The user had said one thing and gotten another, with no intermediate step where the interpretation became visible.

The second category was hallucinated parameters. For API calls the LLM had seen frequently in training, it was accurate. For less common actions — modifying a specific WAF rule set, adjusting a CloudTrail configuration — it would generate plausible-looking parameter values that were simply wrong. Not obviously wrong. Wrong in the way that either silently failed or, worse, silently succeeded with unintended effects.

The third category was irreversibility. AWS IAM changes are fast and consequential. A deleted policy, a disabled account, an SCP modification — these take seconds to execute and can take much longer to diagnose and reverse. An LLM operating with direct API access can generate and execute an irreversible action in a single loop iteration.

## Why "add a confirmation step" doesn't fix it

The obvious fix is to insert a confirmation prompt before execution: "I'm going to disable IAM user john.doe. Confirm?" We tried this. It helps, but it doesn't solve the problem.

By the time you're confirming, you've already done the interpretation. If the LLM interpreted your intent incorrectly — chose the wrong action, hallucinated a parameter value, picked the wrong resource — you're now confirming the wrong action. And the confirmation UX creates pressure to say yes. The system has done the work; you're just rubber-stamping it.

The deeper issue is that a free-form LLM-generated action is hard to review. "I'm going to call `iam:DeleteUser` with `UserName=john.doe`" is more legible than nothing, but it still puts the reviewer in the position of evaluating raw API semantics. Most people — including experienced engineers — can't review a list of IAM API calls and confidently assess the blast radius in real time.

## The architecture that works

What we eventually converged on separates interpretation from execution at a structural level.

The LLM's job is to translate natural language into a typed action. Not to call APIs. Not to decide parameters. To produce a structured, named action from a fixed schema: `disable_iam_user` with `username: john.doe` and `reason: offboarding`. That's it.

The typed action is then:

1. **Validated against a schema.** The `disable_iam_user` action has a defined set of parameters with types and constraints. If the LLM generates a parameter that doesn't exist in the schema, the validation step rejects it before anything reaches the execution layer.

2. **Queued for human approval.** Not a "are you sure?" prompt — a structured review of the typed action that a non-technical approver can read. "Disable IAM user john.doe. Reason: offboarding." An approver doesn't need to know what `iam:DeleteLoginProfile` does to approve or reject this.

3. **Executed by a constrained executor.** The executor knows exactly what `disable_iam_user` does: it calls the right API sequence, captures the before-state, and records everything. It doesn't interpret. It doesn't improvise. It runs a defined operation against a defined target.

This architecture means the LLM can be wrong — and it sometimes is — without causing irreversible damage. The schema validation catches structural errors. The human approval catches semantic errors. The executor is deterministic.

## Why typed actions matter specifically

A typed action — `disable_iam_user` with named parameters — has properties that a raw LLM-generated API call doesn't.

It's **auditable** in plain language. The audit log says "disable_iam_user: john.doe, reason: offboarding, approved by: alice@example.com." Anyone can read that.

It's **rollback-able** because the executor knows what before-state to capture. When `disable_iam_user` runs, it records the user's current state: their access keys, their login profile status, their group memberships, their attached policies. If you need to undo it, you have exactly what you need.

It's **unambiguous** because the schema defines what the action does. There's no interpretation at execution time.

## The specific failure mode we hit

The failure that pushed us most clearly toward typed actions was this: we had an action the LLM had never seen before — rotating database credentials for a specific RDS instance. The LLM generated confident parameter values for the new credential rotation, including a new password that was syntactically valid but violated the database's password policy in a way we hadn't documented in the prompt context.

The action executed, the rotation failed at the database level, but not before the old credentials had been partially invalidated. We had an outage. It was recoverable, but it was avoidable. The executor should never have reached the database with parameters it hadn't validated.

After that, we added schema-level validation on every parameter — length, character set, format — before anything leaves the execution layer. The LLM generates the intent. The schema enforces the constraints. The executor does the work. None of those layers should be doing the others' job.

The lesson isn't that LLMs are dangerous for infrastructure work. It's that the architecture has to treat interpretation and execution as separate concerns with a validation boundary between them.
