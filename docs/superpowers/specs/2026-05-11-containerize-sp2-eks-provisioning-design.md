# SP2 — EKS + ECR Provisioning via Three Methods Design

## Goal
New change types to provision EKS clusters and ECR repositories via Terraform, AWS SDK (boto3), and CloudFormation. Each method registers the cluster as a `kubernetes_cluster` asset and the registry as connector-linked credentials.

## Three Provisioning Paths

### Path A: Terraform
- `eks_cluster_create_terraform` — runs terraform_local_apply with embedded EKS module
- `ecr_repository_create` — shared across paths, uses boto3 (ECR API is simple)
- Terraform state stored in S3 bucket (created if not exists)
- EKS module: AWS managed node group, VPC with public subnets, kubectl access via kubeconfig

### Path B: AWS SDK (boto3)
- `eks_cluster_create_sdk` — boto3 creates EKS cluster, node group, IAM roles, VPC
- Polls cluster status until ACTIVE
- Generates kubeconfig and stores in connector credentials
- Rollback: terminate node group then delete cluster

### Path C: CloudFormation
- `eks_cluster_create_cfn` — creates CloudFormation stack using AWS EKS Quick Start template
- Polls stack status until CREATE_COMPLETE
- Extracts kubeconfig from stack outputs
- Rollback: delete stack (CloudFormation handles dependency ordering)

## Shared: Post-Provisioning Registration
All three paths emit `_auto_asset` for a `kubernetes_cluster` asset with:
- `asset_metadata.endpoint` — K8s API server URL
- `asset_metadata.kubeconfig` — base64 kubeconfig (stored encrypted)
- `asset_metadata.node_count` — current node count
- `asset_metadata.region` — AWS region
- `asset_metadata.provisioner` — "terraform" | "sdk" | "cloudformation"

## ECR Repository
- `ecr_repository_create` — creates ECR repo, returns repository_uri
- `ecr_repository_delete` — rollback
- Auto-creates `container_image` asset stub

## Cost Profile (t3.small × 2 nodes)
- EKS control plane: ~$0.10/hr
- 2× t3.small: ~$0.046/hr
- Total: ~$0.15/hr (~$3.60/day)
- Teardown via rollback after smoke test

## Smoke Test Phases
- Phase EKS-TF: provision via Terraform, verify cluster ACTIVE, run container smoke, teardown
- Phase EKS-SDK: provision via boto3, verify cluster ACTIVE, run container smoke, teardown
- Phase EKS-CFN: provision via CloudFormation, verify cluster ACTIVE, run container smoke, teardown
- Phases can run in parallel (isolated clusters)
