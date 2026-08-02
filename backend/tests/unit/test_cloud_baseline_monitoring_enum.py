# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.models.change_request import ChangeType


def test_aws_baseline_monitoring_enum():
    assert ChangeType.aws_account_baseline_monitoring.value == "aws_account_baseline_monitoring"


def test_gcp_baseline_monitoring_enum():
    assert ChangeType.gcp_account_baseline_monitoring.value == "gcp_account_baseline_monitoring"


def test_azure_baseline_monitoring_enum():
    assert ChangeType.azure_account_baseline_monitoring.value == "azure_account_baseline_monitoring"


def test_oci_baseline_monitoring_enum():
    assert ChangeType.oci_account_baseline_monitoring.value == "oci_account_baseline_monitoring"
