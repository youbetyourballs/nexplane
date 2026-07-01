# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: eks_create_cluster_cfn — create EKS cluster via CloudFormation."""
from __future__ import annotations
import asyncio
import base64
import json


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Create an EKS cluster via CloudFormation quick-start template.

    Parameters:
      - cluster_name (str, required)
      - region (str, optional, default "us-east-1")
      - node_instance_type (str, optional, default "t3.small")
      - node_count (int, optional, default 2)
    """
    import boto3

    cluster_name = parameters.get("cluster_name")
    if not cluster_name:
        raise ValueError("Missing required parameter: cluster_name")

    region = parameters.get("region", "us-east-1")
    node_type = parameters.get("node_instance_type", "t3.small")
    node_count = int(parameters.get("node_count", 2))
    dry_run = bool(parameters.get("dry_run", False))

    creds = getattr(connector, "credentials", {}) or {}
    session = boto3.Session(
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        region_name=region,
    )

    if dry_run:
        return {
            "cluster_name": cluster_name,
            "stack_name": f"nexplane-eks-{cluster_name}",
            "status": "dry_run",
            "endpoint": f"https://DRY-RUN.eks.{region}.amazonaws.com",
            "kubeconfig_b64": base64.b64encode(b"dry-run-kubeconfig").decode(),
            "region": region,
            "provisioner": "cloudformation",
            "_auto_asset": {
                "name": cluster_name,
                "asset_type": "kubernetes_cluster",
                "asset_metadata": {
                    "endpoint": f"https://DRY-RUN.eks.{region}.amazonaws.com",
                    "region": region,
                    "provisioner": "cloudformation",
                    "node_count": node_count,
                    "dry_run": True,
                },
            },
        }

    ec2 = session.client("ec2")
    cfn = session.client("cloudformation")
    eks = session.client("eks")

    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpc_id = vpcs["Vpcs"][0]["VpcId"]
    subnets_resp = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    subnet_ids = [s["SubnetId"] for s in subnets_resp["Subnets"][:2]]

    stack_name = f"nexplane-eks-{cluster_name}"
    # Inline CFN template for a simple EKS cluster
    template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": f"Nexplane EKS cluster {cluster_name}",
        "Resources": {
            "EKSRole": {
                "Type": "AWS::IAM::Role",
                "Properties": {
                    "RoleName": f"nexplane-cfn-eks-{cluster_name}",
                    "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "eks.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
                    "ManagedPolicyArns": ["arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"],
                },
            },
            "EKSCluster": {
                "Type": "AWS::EKS::Cluster",
                "Properties": {
                    "Name": cluster_name,
                    "RoleArn": {"Fn::GetAtt": ["EKSRole", "Arn"]},
                    "ResourcesVpcConfig": {"SubnetIds": subnet_ids},
                },
                "DependsOn": "EKSRole",
            },
        },
        "Outputs": {
            "ClusterName": {"Value": {"Ref": "EKSCluster"}},
        },
    }

    cfn.create_stack(
        StackName=stack_name,
        TemplateBody=json.dumps(template),
        Capabilities=["CAPABILITY_NAMED_IAM"],
    )

    # Poll until CREATE_COMPLETE (up to 25 minutes)
    for _ in range(150):
        stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
        status = stack["StackStatus"]
        if status == "CREATE_COMPLETE":
            break
        if "FAILED" in status or "ROLLBACK" in status:
            raise RuntimeError(f"CloudFormation stack failed: {status}")
        await asyncio.sleep(10)
    else:
        raise RuntimeError(f"CloudFormation stack {stack_name} did not complete within 25 minutes")

    cluster = eks.describe_cluster(name=cluster_name)["cluster"]
    endpoint = cluster["endpoint"]
    ca_data = cluster["certificateAuthority"]["data"]

    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
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
        "stack_name": stack_name,
        "status": "active",
        "endpoint": endpoint,
        "kubeconfig_b64": kubeconfig_b64,
        "region": region,
        "provisioner": "cloudformation",
        "_auto_asset": {
            "name": cluster_name,
            "asset_type": "kubernetes_cluster",
            "asset_metadata": {
                "endpoint": endpoint,
                "region": region,
                "provisioner": "cloudformation",
                "node_count": node_count,
                "kubeconfig_b64": kubeconfig_b64,
            },
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    import boto3
    stack_name = execution_result.get("stack_name")
    region = execution_result.get("region", "us-east-1")
    if not stack_name or execution_result.get("status") == "dry_run":
        return {"rolled_back": False}
    creds = getattr(connector, "credentials", {}) or {}
    session = boto3.Session(
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        region_name=region,
    )
    cfn = session.client("cloudformation")
    try:
        cfn.delete_stack(StackName=stack_name)
        return {"rolled_back": True, "stack_name": stack_name}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
