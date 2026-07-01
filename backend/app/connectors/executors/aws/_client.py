# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import boto3


def get_ec2_client(creds: dict):
    return boto3.client(
        'ec2',
        aws_access_key_id=creds.get('access_key_id'),
        aws_secret_access_key=creds.get('secret_access_key'),
        region_name=creds.get('region', 'us-east-1'),
        aws_session_token=creds.get('session_token') or None,
    )


def get_iam_client(creds: dict):
    return boto3.client(
        'iam',
        aws_access_key_id=creds.get('access_key_id'),
        aws_secret_access_key=creds.get('secret_access_key'),
        region_name=creds.get('region', 'us-east-1'),
        aws_session_token=creds.get('session_token') or None,
    )


def get_boto3_client(creds: dict, service: str):
    return boto3.client(
        service,
        aws_access_key_id=creds.get('access_key_id'),
        aws_secret_access_key=creds.get('secret_access_key'),
        region_name=creds.get('region', 'us-east-1'),
        aws_session_token=creds.get('session_token') or None,
    )
