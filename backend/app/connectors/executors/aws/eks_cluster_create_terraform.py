# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: eks_create_cluster_terraform — create EKS cluster via terraform_local_apply."""
from __future__ import annotations
import asyncio
import base64
import json
import os
import tempfile


TERRAFORM_EKS_MODULE = '''
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.region
}

variable "cluster_name" {}
variable "region"       { default = "us-east-1" }
variable "node_type"    { default = "t3.small" }
variable "node_count"   { default = 2 }

data "aws_vpc" "default" { default = true }

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_iam_role" "eks" {
  name = "nexplane-tf-eks-${var.cluster_name}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "eks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_policy" {
  role       = aws_iam_role.eks.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_eks_cluster" "main" {
  name     = var.cluster_name
  role_arn = aws_iam_role.eks.arn
  vpc_config { subnet_ids = slice(tolist(data.aws_subnets.default.ids), 0, 2) }
  depends_on = [aws_iam_role_policy_attachment.eks_policy]
}

output "endpoint"       { value = aws_eks_cluster.main.endpoint }
output "ca_data"        { value = aws_eks_cluster.main.certificate_authority[0].data }
output "cluster_name"   { value = aws_eks_cluster.main.name }
'''


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    cluster_name = parameters.get("cluster_name")
    if not cluster_name:
        raise ValueError("Missing required parameter: cluster_name")

    region = parameters.get("region", "us-east-1")
    node_type = parameters.get("node_instance_type", "t3.small")
    node_count = int(parameters.get("node_count", 2))
    dry_run = bool(parameters.get("dry_run", False))

    creds = getattr(connector, "credentials", {}) or {}

    if dry_run:
        return {
            "cluster_name": cluster_name,
            "status": "dry_run",
            "endpoint": f"https://DRY-RUN.eks.{region}.amazonaws.com",
            "kubeconfig_b64": base64.b64encode(b"dry-run-kubeconfig").decode(),
            "region": region,
            "provisioner": "terraform",
            "_auto_asset": {
                "name": cluster_name,
                "asset_type": "kubernetes_cluster",
                "asset_metadata": {
                    "endpoint": f"https://DRY-RUN.eks.{region}.amazonaws.com",
                    "region": region,
                    "provisioner": "terraform",
                    "node_count": node_count,
                    "dry_run": True,
                },
            },
        }

    import subprocess
    env = os.environ.copy()
    env.update({
        "AWS_ACCESS_KEY_ID": creds.get("aws_access_key_id", ""),
        "AWS_SECRET_ACCESS_KEY": creds.get("aws_secret_access_key", ""),
        "AWS_DEFAULT_REGION": region,
    })

    with tempfile.TemporaryDirectory() as tmpdir:
        tf_file = os.path.join(tmpdir, "main.tf")
        with open(tf_file, "w") as f:
            f.write(TERRAFORM_EKS_MODULE)

        tfvars = {
            "cluster_name": cluster_name,
            "region": region,
            "node_type": node_type,
            "node_count": node_count,
        }
        tfvars_file = os.path.join(tmpdir, "terraform.tfvars.json")
        with open(tfvars_file, "w") as f:
            json.dump(tfvars, f)

        def _run(cmd):
            result = subprocess.run(cmd, cwd=tmpdir, env=env, capture_output=True, text=True, timeout=900)
            if result.returncode != 0:
                raise RuntimeError(f"terraform {cmd[1]} failed: {result.stderr[-2000:]}")
            return result

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _run(["terraform", "init", "-no-color"]))
        await loop.run_in_executor(None, lambda: _run(["terraform", "apply", "-auto-approve", "-no-color"]))

        out_result = await loop.run_in_executor(None, lambda: subprocess.run(
            ["terraform", "output", "-json"], cwd=tmpdir, env=env, capture_output=True, text=True
        ))
        outputs = json.loads(out_result.stdout)
        endpoint = outputs["endpoint"]["value"]
        ca_data = outputs["ca_data"]["value"]

    kubeconfig = {
        "apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": cluster_name, "cluster": {"server": endpoint, "certificate-authority-data": ca_data}}],
        "contexts": [{"name": cluster_name, "context": {"cluster": cluster_name, "user": cluster_name}}],
        "current-context": cluster_name,
        "users": [{"name": cluster_name, "user": {"exec": {
            "apiVersion": "client.authentication.k8s.io/v1beta1",
            "command": "aws",
            "args": ["eks", "get-token", "--cluster-name", cluster_name, "--region", region],
        }}}],
    }
    kubeconfig_b64 = base64.b64encode(json.dumps(kubeconfig).encode()).decode()

    return {
        "cluster_name": cluster_name,
        "status": "active",
        "endpoint": endpoint,
        "kubeconfig_b64": kubeconfig_b64,
        "region": region,
        "provisioner": "terraform",
        "_auto_asset": {
            "name": cluster_name,
            "asset_type": "kubernetes_cluster",
            "asset_metadata": {
                "endpoint": endpoint,
                "region": region,
                "provisioner": "terraform",
                "node_count": node_count,
                "kubeconfig_b64": kubeconfig_b64,
            },
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    cluster_name = execution_result.get("cluster_name")
    region = execution_result.get("region", "us-east-1")
    if not cluster_name or execution_result.get("status") == "dry_run":
        return {"rolled_back": False}
    import boto3
    creds = getattr(connector, "credentials", {}) or {}
    session = boto3.Session(
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        region_name=region,
    )
    eks = session.client("eks")
    try:
        eks.delete_cluster(name=cluster_name)
        return {"rolled_back": True, "cluster_name": cluster_name}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
