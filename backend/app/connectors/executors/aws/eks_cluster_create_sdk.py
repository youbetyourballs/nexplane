# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: eks_create_cluster_sdk — create EKS cluster via boto3."""
from __future__ import annotations
import asyncio
import base64
import json

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Create an EKS cluster and managed node group via boto3.

    Parameters:
      - cluster_name (str, required)
      - region (str, optional, default "us-east-1")
      - kubernetes_version (str, optional, default "1.29")
      - node_instance_type (str, optional, default "t3.small")
      - node_count (int, optional, default 2)
    """
    import boto3

    cluster_name = parameters.get("cluster_name")
    if not cluster_name:
        raise ValueError("Missing required parameter: cluster_name")

    region = parameters.get("region", "us-east-1")
    k8s_version = parameters.get("kubernetes_version", "1.29")
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
            "status": "dry_run",
            "endpoint": f"https://DRY-RUN.eks.{region}.amazonaws.com",
            "kubeconfig_b64": base64.b64encode(b"dry-run-kubeconfig").decode(),
            "region": region,
            "provisioner": "sdk",
            "_auto_asset": {
                "name": cluster_name,
                "asset_type": "kubernetes_cluster",
                "asset_metadata": {
                    "endpoint": f"https://DRY-RUN.eks.{region}.amazonaws.com",
                    "region": region,
                    "provisioner": "sdk",
                    "node_count": node_count,
                    "dry_run": True,
                },
            },
        }

    eks = session.client("eks")
    ec2 = session.client("ec2")
    iam = session.client("iam")

    # Get default VPC subnets
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpc_id = vpcs["Vpcs"][0]["VpcId"]
    subnets_resp = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    subnet_ids = [s["SubnetId"] for s in subnets_resp["Subnets"][:3]]

    # Ensure EKS service role
    role_name = f"nexplane-eks-role-{cluster_name}"
    try:
        role = iam.get_role(RoleName=role_name)
        role_arn = role["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        trust = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "eks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        })
        role = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=trust)
        role_arn = role["Role"]["Arn"]
        iam.attach_role_policy(RoleName=role_name, PolicyArn="arn:aws:iam::aws:policy/AmazonEKSClusterPolicy")

    # Create cluster
    eks.create_cluster(
        name=cluster_name,
        version=k8s_version,
        roleArn=role_arn,
        resourcesVpcConfig={"subnetIds": subnet_ids},
    )

    # Wait for ACTIVE (up to 20 minutes)
    for _ in range(120):
        cluster = eks.describe_cluster(name=cluster_name)["cluster"]
        if cluster["status"] == "ACTIVE":
            break
        await asyncio.sleep(10)
    else:
        raise RuntimeError(f"EKS cluster {cluster_name} did not become ACTIVE within 20 minutes")

    endpoint = cluster["endpoint"]
    ca_data = cluster["certificateAuthority"]["data"]

    # Node group IAM role
    ng_role_name = f"nexplane-eks-node-{cluster_name}"
    try:
        ng_role = iam.get_role(RoleName=ng_role_name)
        ng_role_arn = ng_role["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        trust = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        })
        ng_role = iam.create_role(RoleName=ng_role_name, AssumeRolePolicyDocument=trust)
        ng_role_arn = ng_role["Role"]["Arn"]
        for policy in [
            "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
            "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
            "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
        ]:
            iam.attach_role_policy(RoleName=ng_role_name, PolicyArn=policy)

    eks.create_nodegroup(
        clusterName=cluster_name,
        nodegroupName=f"{cluster_name}-nodes",
        nodeRole=ng_role_arn,
        subnets=subnet_ids,
        instanceTypes=[node_type],
        scalingConfig={"minSize": node_count, "maxSize": node_count, "desiredSize": node_count},
    )

    # Build kubeconfig
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
        "status": "active",
        "endpoint": endpoint,
        "kubeconfig_b64": kubeconfig_b64,
        "region": region,
        "provisioner": "sdk",
        "_auto_asset": {
            "name": cluster_name,
            "asset_type": "kubernetes_cluster",
            "asset_metadata": {
                "endpoint": endpoint,
                "region": region,
                "provisioner": "sdk",
                "node_count": node_count,
                "kubeconfig_b64": kubeconfig_b64,
            },
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    import boto3
    cluster_name = execution_result.get("cluster_name")
    region = execution_result.get("region", "us-east-1")
    if not cluster_name or execution_result.get("status") == "dry_run":
        return {"rolled_back": False}

    creds = getattr(connector, "credentials", {}) or {}
    session = boto3.Session(
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        region_name=region,
    )
    eks = session.client("eks")
    try:
        eks.delete_nodegroup(clusterName=cluster_name, nodegroupName=f"{cluster_name}-nodes")
        import asyncio
        await asyncio.sleep(60)
    except Exception:
        pass
    try:
        eks.delete_cluster(name=cluster_name)
    except Exception:
        pass
    return {"rolled_back": True, "cluster_name": cluster_name}
