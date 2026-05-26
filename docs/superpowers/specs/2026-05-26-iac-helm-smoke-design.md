# IaC + Helm Smoke Coverage Design

> **Scope:** Group A of Tier 2 smoke backlog. Three new smoke phases covering `cloudformation`, `bicep`, and `helm`. Groups B (checkov, chef_inspec) and C (azure_ad, defender_endpoint) are separate sessions.

**Goal:** Add live smoke coverage for three unverified domains by mirroring existing IaC patterns and extending the k8s smoke infra already in place.

**Method:** CR pipeline throughout (dogfooding principle). No new infra to provision — CloudFormation uses AWS credentials already in DB, Bicep reuses the Azure resource group from test_azure_live.py, Helm reuses the kind cluster AMI from the K8S_RBAC phase.

---

## Executor Change

### `backend/app/connectors/executors/cloudformation/create_change_set.py`

Add a `change_set_type` parameter (string, default `"UPDATE"`). Pass it as `ChangeSetType` to the boto3 `create_change_set` call. This is the only production code change in this session — without it, there is no way to create a net-new CloudFormation stack through the CR pipeline.

Backwards compatible: existing CRs that omit `change_set_type` continue to behave as UPDATE.

---

## Phase CF — CloudFormation (`test_aws_live.py`)

**Infra:** AWS credentials from DB (already used in Phase C, VULN_MITIGATION, CREDENTIAL_EXPIRY). No new connector needed.

**Template:** Minimal SNS topic (fast, free, rollback-safe):
```json
{
  "AWSTemplateFormatVersion": "2010-09-09",
  "Resources": {
    "SmokeTopic": {
      "Type": "AWS::SNS::Topic",
      "Properties": { "TopicName": "<random-name>" }
    }
  }
}
```

**CR sequence (all through Nexplane pipeline):**

1. `create_change_set` — `change_set_type=CREATE`, `stack_name=nexplane-smoke-cf-<suffix>`, `template_body=<above>` → returns `change_set_name`
2. `execute_change_set` — `stack_name=<above>`, `change_set_name=<from step 1>` → stack created
3. `discover_stacks` — assert our stack appears in results with status `CREATE_COMPLETE`
4. `delete_stack` — `stack_name=<above>` → cleanup; assert stack gone via boto3 SDK check

No CR rollback needed — `delete_stack` is the cleanup step.

---

## Phase BICEP — Bicep (`test_azure_live.py`)

**Infra:** Azure credentials from DB (already used throughout test_azure_live.py). Uses the existing `azure_resource_group` value present in that file.

**Template:** Minimal ARM JSON — creates a storage account (simple, rollback-safe, cheap):
```json
{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "storageAccountName": { "type": "string" }
  },
  "resources": [
    {
      "type": "Microsoft.Storage/storageAccounts",
      "apiVersion": "2022-09-01",
      "name": "[parameters('storageAccountName')]",
      "location": "[resourceGroup().location]",
      "sku": { "name": "Standard_LRS" },
      "kind": "StorageV2"
    }
  ]
}
```

Storage account name: `nexsmk<8-char hex suffix>` (must be globally unique, lowercase, max 24 chars).

**CR sequence:**

1. `create_deployment` — `resource_group=<existing>`, `deployment_name=nexplane-smoke-bicep-<suffix>`, `template=<above>`, `parameters={"storageAccountName": {"value": "<name>"}}` → deployment created
2. `discover_deployments` — assert our deployment appears with status `Succeeded`
3. `delete_deployment` — `resource_group=<existing>`, `deployment_name=<above>` → cleanup; assert deployment gone via Azure SDK check

Rollback: explicit `delete_deployment` CR (same pattern as terraform_local uses `terraform_destroy_local`). The `create_deployment` executor's `rollback()` method deliberately does not automate this, so cleanup is an explicit CR.

---

## Phase HELM — Helm (`test_aws_live.py`)

**Infra:** Reuses the kind cluster EC2 AMI cached by Phase K8S_RBAC. The k8s connector registered in that phase is reused here — no new connector registration.

**Setup (SSM command on the AMI instance):**
```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
helm install smoke-nginx bitnami/nginx --namespace default --wait --timeout 120s
```

Chart: `bitnami/nginx` — small, fast, no persistent volumes, uninstalls cleanly.

**CR sequence (using the k8s connector from K8S_RBAC):**

1. `discover_releases` — assert `smoke-nginx` appears with status `deployed`
2. `rollback_release` — `release_name=smoke-nginx`, `namespace=default`, `revision=0` (rollback to previous revision — exercises the executor path; on a single-revision release this is a no-op reinstall)
3. `uninstall_release` — `release_name=smoke-nginx`, `namespace=default` → cleanup; assert release no longer appears in `discover_releases`

`helm` is confirmed installed in the backend container image (`get-helm-3` in Dockerfile).

---

## Files

| Action | Path |
|--------|------|
| Modify | `backend/app/connectors/executors/cloudformation/create_change_set.py` |
| Modify | `backend/tests/smoke/test_aws_live.py` |
| Modify | `backend/tests/smoke/test_azure_live.py` |

No new files. No migrations. No model changes.

---

## Non-Goals

- Groups B and C (checkov, chef_inspec, azure_ad, defender_endpoint) — separate sessions
- Implementing automated rollback in `bicep/create_deployment.py` — deferred
- Adding `install_release` executor — deferred; not needed for this smoke coverage
