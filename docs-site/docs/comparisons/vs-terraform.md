# Nexplane vs. Terraform

Terraform and Nexplane address different phases of the infrastructure lifecycle. They are designed to complement each other, not compete.

## What Terraform is

Terraform is a declarative infrastructure-as-code tool. You describe the desired state of your infrastructure in HCL, and Terraform figures out the sequence of API calls needed to get there. Its core strengths are:

- **Provisioning and deprovisioning** — Create and destroy cloud resources (VPCs, VMs, databases, IAM roles) in a repeatable, version-controlled way.
- **Drift detection** — `terraform plan` compares the desired state against actual infrastructure and shows what is out of sync.
- **State tracking** — Terraform maintains a state file that records what resources it manages, enabling incremental updates.
- **Repeatable infrastructure** — The same configuration applied to different environments produces consistent results.

## The gap: rollback, approval, and security operations

Terraform's rollback model is "apply a previous version." If you need to undo a change, you check out an older version of the configuration and run `terraform apply` again. This re-provisions resources to match the older desired state — it does not undo the specific operation that was executed. For many infrastructure scenarios this is acceptable. For security operations it is not:

- **Re-provisioning is not undoing.** If an IAM user was disabled by modifying a Terraform resource, re-enabling them means changing the HCL and re-applying. This works, but it re-runs the entire module, not just the one operation — and it requires a code change, a commit, and a pipeline run.
- **No per-operation snapshot.** Terraform does not capture the exact before-state of a resource at the moment a change was applied. The state file tracks what Terraform manages, but not what the resource looked like immediately before the last `apply`.
- **No approval workflow.** Terraform itself has no concept of "a human must approve this plan before it executes." Tools like Atlantis add this capability, but it is not built into Terraform and does not model the change request as a first-class object with a lifecycle.
- **No change request audit trail.** Terraform logs runs, but it does not record who approved a specific change, what the before-state was at the moment of execution, or provide a rollback mechanism scoped to exactly one change.

## Where Nexplane fits alongside Terraform

Nexplane includes a **Terraform connector**. The two tools address different parts of the infrastructure lifecycle:

| Phase | Tool | Example |
|---|---|---|
| Provisioning new resources | Terraform | Create an S3 bucket, provision an RDS instance, define an IAM role |
| Post-provisioning security operations | Nexplane | Rotate the RDS password, disable the IAM user, revoke a role binding |
| Compliance hardening | Nexplane | Apply a security group rule change with documented approval and rollback |
| Drift correction at scale | Terraform | Detect and remediate infrastructure drift across accounts |

Infrastructure provisioning belongs in Terraform. Security operations on that infrastructure — changes that are sensitive, time-pressured, and must be reversible — belong in Nexplane.

## The specific gap Nexplane fills

Consider this scenario: a security analyst receives an alert that an IAM user's credentials may be compromised. They need to:

1. Disable the user immediately
2. Have an auditable record that the security lead approved the action
3. Be able to re-enable the user in 30 seconds if the alert turns out to be a false positive

This is not a Terraform use case. It requires:

- A change request created with typed parameters (`disable_iam_user`, target account, target user)
- An approval gate before execution
- A before-state snapshot (the user's current status, attached policies, last activity)
- One-click rollback that re-enables exactly this user without touching anything else

Nexplane handles this. The Terraform state file for this user's IAM resources is unaffected; the change is tracked entirely within Nexplane's change request record.

## When to use Terraform alone

- Provisioning net-new infrastructure where no approval gate is required and rollback means destroying or re-creating resources
- Configuration drift correction across environments via CI/CD pipelines
- Managing infrastructure that is fully described in code and where re-applying is an acceptable remediation path

## When Nexplane adds value alongside Terraform

- Security-sensitive changes to existing resources that require documented approval and precise rollback
- Operations triggered by a security event where speed and reversibility both matter
- Environments with compliance requirements (SOC 2, PCI DSS, ISO 27001) that mandate an approval trail for changes to production systems
- Any change where "re-apply the old Terraform config" is too blunt — you need to undo exactly one operation, not re-converge an entire module
