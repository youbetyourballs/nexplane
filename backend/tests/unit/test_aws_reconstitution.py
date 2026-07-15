# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeConnector:
    def __init__(self, creds=None):
        self.credentials = creds or {"region": "us-east-1"}


# ---------------------------------------------------------------------------
# delete_route53_record
# ---------------------------------------------------------------------------
class TestDeleteRoute53RecordRollback(unittest.TestCase):
    def _make_mock_r53(self):
        mock_r53 = MagicMock()
        mock_r53.list_resource_record_sets.return_value = {
            "ResourceRecordSets": [
                {
                    "Name": "test.example.com.",
                    "Type": "A",
                    "TTL": 300,
                    "ResourceRecords": [{"Value": "1.2.3.4"}],
                }
            ]
        }
        mock_r53.change_resource_record_sets.return_value = {
            "ChangeInfo": {"Id": "/change/C123"}
        }
        return mock_r53

    def test_execute_captures_pre_state(self):
        mock_r53 = self._make_mock_r53()
        with patch(
            "app.connectors.executors.aws.delete_route53_record.get_boto3_client",
            return_value=mock_r53,
        ):
            from app.connectors.executors.aws import delete_route53_record
            result = run(
                delete_route53_record.execute(
                    {"hosted_zone_id": "Z123", "name": "test.example.com", "record_type": "A"},
                    [],
                    FakeConnector(),
                )
            )
        self.assertIn("pre_state", result)
        self.assertIn("prior_record", result["pre_state"])

    def test_rollback_restores_record(self):
        mock_r53 = self._make_mock_r53()
        with patch(
            "app.connectors.executors.aws.delete_route53_record.get_boto3_client",
            return_value=mock_r53,
        ):
            from app.connectors.executors.aws import delete_route53_record
            execution_result = {
                "zone_id": "Z123",
                "name": "test.example.com",
                "record_type": "A",
                "pre_state": {
                    "prior_record": {
                        "Name": "test.example.com.",
                        "Type": "A",
                        "TTL": 300,
                        "ResourceRecords": [{"Value": "1.2.3.4"}],
                    }
                },
            }
            result = run(
                delete_route53_record.rollback(
                    {"hosted_zone_id": "Z123", "name": "test.example.com"},
                    execution_result,
                    FakeConnector(),
                )
            )
        self.assertTrue(result["rolled_back"])
        mock_r53.change_resource_record_sets.assert_called_once()
        call_args = mock_r53.change_resource_record_sets.call_args[1]
        action = call_args["ChangeBatch"]["Changes"][0]["Action"]
        self.assertEqual(action, "UPSERT")

    def test_rollback_no_pre_state_returns_false(self):
        with patch(
            "app.connectors.executors.aws.delete_route53_record.get_boto3_client",
            return_value=MagicMock(),
        ):
            from app.connectors.executors.aws import delete_route53_record
            result = run(
                delete_route53_record.rollback({}, {"pre_state": {}}, FakeConnector())
            )
        self.assertFalse(result["rolled_back"])
        self.assertIn("reason", result)


# ---------------------------------------------------------------------------
# delete_cloudwatch_alarm
# ---------------------------------------------------------------------------
class TestDeleteCloudwatchAlarmRollback(unittest.TestCase):
    def _alarm_data(self):
        return {
            "AlarmName": "my-alarm",
            "MetricName": "CPUUtilization",
            "Namespace": "AWS/EC2",
            "Statistic": "Average",
            "Period": 300,
            "EvaluationPeriods": 2,
            "Threshold": 80.0,
            "ComparisonOperator": "GreaterThanThreshold",
            "AlarmDescription": "CPU high",
            "Dimensions": [{"Name": "InstanceId", "Value": "i-abc"}],
            "AlarmActions": ["arn:aws:sns:us-east-1:123:MyTopic"],
            "OKActions": [],
        }

    def test_execute_captures_pre_state(self):
        mock_cw = MagicMock()
        mock_cw.describe_alarms.return_value = {"MetricAlarms": [self._alarm_data()]}
        with patch(
            "app.connectors.executors.aws.delete_cloudwatch_alarm.get_boto3_client",
            return_value=mock_cw,
        ):
            from app.connectors.executors.aws import delete_cloudwatch_alarm
            result = run(
                delete_cloudwatch_alarm.execute(
                    {"alarm_name": "my-alarm"}, [], FakeConnector()
                )
            )
        self.assertIn("pre_state", result)
        self.assertIsNotNone(result["pre_state"].get("alarm"))

    def test_rollback_calls_put_metric_alarm(self):
        mock_cw = MagicMock()
        alarm = self._alarm_data()
        with patch(
            "app.connectors.executors.aws.delete_cloudwatch_alarm.get_boto3_client",
            return_value=mock_cw,
        ):
            from app.connectors.executors.aws import delete_cloudwatch_alarm
            result = run(
                delete_cloudwatch_alarm.rollback(
                    {},
                    {"pre_state": {"alarm": alarm}},
                    FakeConnector(),
                )
            )
        self.assertTrue(result["rolled_back"])
        mock_cw.put_metric_alarm.assert_called_once()
        call_kwargs = mock_cw.put_metric_alarm.call_args[1]
        self.assertEqual(call_kwargs["AlarmName"], "my-alarm")
        self.assertEqual(call_kwargs["MetricName"], "CPUUtilization")

    def test_rollback_no_pre_state_returns_false(self):
        with patch(
            "app.connectors.executors.aws.delete_cloudwatch_alarm.get_boto3_client",
            return_value=MagicMock(),
        ):
            from app.connectors.executors.aws import delete_cloudwatch_alarm
            result = run(
                delete_cloudwatch_alarm.rollback({}, {"pre_state": {}}, FakeConnector())
            )
        self.assertFalse(result["rolled_back"])


# ---------------------------------------------------------------------------
# put_bucket_policy
# ---------------------------------------------------------------------------
class TestPutBucketPolicyRollback(unittest.TestCase):
    def test_execute_captures_prior_policy(self):
        prior = json.dumps({"Version": "2012-10-17", "Statement": []})
        mock_s3 = MagicMock()
        mock_s3.get_bucket_policy.return_value = {"Policy": prior}
        with patch(
            "app.connectors.executors.aws.put_bucket_policy.get_boto3_client",
            return_value=mock_s3,
        ):
            from app.connectors.executors.aws import put_bucket_policy
            result = run(
                put_bucket_policy.execute(
                    {"bucket": "my-bucket", "policy": '{"Statement": [{"Effect": "Allow"}]}'}, [], FakeConnector()
                )
            )
        self.assertEqual(result["pre_state"]["policy"], prior)

    def test_execute_captures_none_when_no_policy(self):
        from botocore.exceptions import ClientError
        mock_s3 = MagicMock()
        mock_s3.get_bucket_policy.side_effect = ClientError(
            {"Error": {"Code": "NoSuchBucketPolicy", "Message": ""}}, "GetBucketPolicy"
        )
        with patch(
            "app.connectors.executors.aws.put_bucket_policy.get_boto3_client",
            return_value=mock_s3,
        ):
            from app.connectors.executors.aws import put_bucket_policy
            result = run(
                put_bucket_policy.execute(
                    {"bucket": "my-bucket", "policy": "{}"}, [], FakeConnector()
                )
            )
        self.assertIsNone(result["pre_state"]["policy"])

    def test_rollback_restores_prior_policy(self):
        prior = json.dumps({"Version": "2012-10-17", "Statement": []})
        mock_s3 = MagicMock()
        with patch(
            "app.connectors.executors.aws.put_bucket_policy.get_boto3_client",
            return_value=mock_s3,
        ):
            from app.connectors.executors.aws import put_bucket_policy
            result = run(
                put_bucket_policy.rollback(
                    {"bucket": "my-bucket"},
                    {"bucket": "my-bucket", "pre_state": {"policy": prior}},
                    FakeConnector(),
                )
            )
        self.assertTrue(result["rolled_back"])
        mock_s3.put_bucket_policy.assert_called_once_with(Bucket="my-bucket", Policy=prior)

    def test_rollback_deletes_policy_when_prior_was_none(self):
        mock_s3 = MagicMock()
        with patch(
            "app.connectors.executors.aws.put_bucket_policy.get_boto3_client",
            return_value=mock_s3,
        ):
            from app.connectors.executors.aws import put_bucket_policy
            result = run(
                put_bucket_policy.rollback(
                    {"bucket": "my-bucket"},
                    {"bucket": "my-bucket", "pre_state": {"policy": None}},
                    FakeConnector(),
                )
            )
        self.assertTrue(result["rolled_back"])
        mock_s3.delete_bucket_policy.assert_called_once_with(Bucket="my-bucket")

    def test_rollback_no_pre_state_returns_false(self):
        with patch(
            "app.connectors.executors.aws.put_bucket_policy.get_boto3_client",
            return_value=MagicMock(),
        ):
            from app.connectors.executors.aws import put_bucket_policy
            result = run(
                put_bucket_policy.rollback({}, {"pre_state": {}}, FakeConnector())
            )
        self.assertFalse(result["rolled_back"])


# ---------------------------------------------------------------------------
# modify_listener
# ---------------------------------------------------------------------------
class TestModifyListenerRollback(unittest.TestCase):
    def _listener_data(self):
        return {
            "ListenerArn": "arn:aws:elasticloadbalancing:us-east-1:123:listener/app/my-alb/abc/def",
            "Protocol": "HTTPS",
            "Port": 443,
            "SslPolicy": "ELBSecurityPolicy-2016-08",
            "DefaultActions": [{"Type": "forward", "TargetGroupArn": "arn:aws:...:tg/old-tg/123"}],
            "Certificates": [{"CertificateArn": "arn:aws:acm:us-east-1:123:certificate/xyz"}],
        }

    def test_execute_captures_prior_listener(self):
        mock_elb = MagicMock()
        mock_elb.describe_listeners.return_value = {"Listeners": [self._listener_data()]}
        with patch(
            "app.connectors.executors.aws.modify_listener.get_boto3_client",
            return_value=mock_elb,
        ):
            from app.connectors.executors.aws import modify_listener
            result = run(
                modify_listener.execute(
                    {
                        "listener_arn": "arn:aws:...",
                        "port": 8443,
                    },
                    [],
                    FakeConnector(),
                )
            )
        self.assertIn("listener", result["pre_state"])

    def test_rollback_restores_prior_config(self):
        mock_elb = MagicMock()
        listener = self._listener_data()
        with patch(
            "app.connectors.executors.aws.modify_listener.get_boto3_client",
            return_value=mock_elb,
        ):
            from app.connectors.executors.aws import modify_listener
            result = run(
                modify_listener.rollback(
                    {},
                    {
                        "listener_arn": listener["ListenerArn"],
                        "pre_state": {"listener": listener},
                    },
                    FakeConnector(),
                )
            )
        self.assertTrue(result["rolled_back"])
        mock_elb.modify_listener.assert_called_once()
        call_kwargs = mock_elb.modify_listener.call_args[1]
        self.assertEqual(call_kwargs["Protocol"], "HTTPS")
        self.assertEqual(call_kwargs["Port"], 443)

    def test_rollback_no_pre_state_returns_false(self):
        with patch(
            "app.connectors.executors.aws.modify_listener.get_boto3_client",
            return_value=MagicMock(),
        ):
            from app.connectors.executors.aws import modify_listener
            result = run(
                modify_listener.rollback({}, {"pre_state": {}}, FakeConnector())
            )
        self.assertFalse(result["rolled_back"])


# ---------------------------------------------------------------------------
# lock_iam_user
# ---------------------------------------------------------------------------
class TestLockIamUserRollback(unittest.TestCase):
    def test_execute_captures_key_statuses(self):
        mock_iam = MagicMock()
        mock_iam.list_access_keys.return_value = {
            "AccessKeyMetadata": [
                {"AccessKeyId": "AKIA1", "Status": "Active"},
                {"AccessKeyId": "AKIA2", "Status": "Inactive"},
            ]
        }
        with patch(
            "app.connectors.executors.aws.lock_iam_user.get_boto3_client",
            return_value=mock_iam,
        ):
            from app.connectors.executors.aws import lock_iam_user
            result = run(
                lock_iam_user.execute({"username": "bob"}, [], FakeConnector())
            )
        self.assertEqual(len(result["pre_state"]["access_keys"]), 2)
        self.assertEqual(result["pre_state"]["access_keys"][0]["Status"], "Active")

    def test_rollback_removes_policy_and_restores_active_keys(self):
        mock_iam = MagicMock()
        with patch(
            "app.connectors.executors.aws.lock_iam_user.get_boto3_client",
            return_value=mock_iam,
        ):
            from app.connectors.executors.aws import lock_iam_user
            result = run(
                lock_iam_user.rollback(
                    {"username": "bob"},
                    {
                        "username": "bob",
                        "pre_state": {
                            "access_keys": [
                                {"AccessKeyId": "AKIA1", "Status": "Active"},
                                {"AccessKeyId": "AKIA2", "Status": "Inactive"},
                            ]
                        },
                    },
                    FakeConnector(),
                )
            )
        self.assertTrue(result["rolled_back"])
        mock_iam.delete_user_policy.assert_called_once_with(
            UserName="bob", PolicyName="nexplane-emergency-lockout"
        )
        # Only AKIA1 was Active — should be restored
        self.assertIn("AKIA1", result["keys_restored_to_active"])
        self.assertNotIn("AKIA2", result["keys_restored_to_active"])

    def test_rollback_no_pre_state_returns_false(self):
        with patch(
            "app.connectors.executors.aws.lock_iam_user.get_boto3_client",
            return_value=MagicMock(),
        ):
            from app.connectors.executors.aws import lock_iam_user
            result = run(
                lock_iam_user.rollback({}, {"pre_state": {}}, FakeConnector())
            )
        self.assertFalse(result["rolled_back"])


if __name__ == "__main__":
    unittest.main()
