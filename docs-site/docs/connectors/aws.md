# AWS Connector

The AWS connector uses the AWS SDK to execute and roll back IAM, S3, and related security operations against one or more AWS accounts.

## Credential fields

| Field | Required | Description |
|---|---|---|
| `access_key_id` | Yes | AWS access key ID for the Nexplane service account |
| `secret_access_key` | Yes | Corresponding AWS secret access key |
| `region` | Yes | Default AWS region (e.g. `us-east-1`) |
| `account_id` | Yes | 12-digit AWS account ID — used for validation and logging |

### Minimum IAM permissions

The Nexplane service account needs only the permissions required for the change types you intend to use. A least-privilege policy example for the full action set:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "iam:GetUser",
        "iam:UpdateLoginProfile",
        "iam:CreateAccessKey",
        "iam:DeleteAccessKey",
        "iam:ListAccessKeys",
        "iam:AttachUserPolicy",
        "iam:DetachUserPolicy",
        "s3:PutBucketPublicAccessBlock",
        "s3:GetBucketPublicAccessBlock"
      ],
      "Resource": "*"
    }
  ]
}
```

## Supported change types

### `disable_iam_user`

Disables an IAM user's console login profile and deactivates all active access keys.

| Parameter | Type | Description |
|---|---|---|
| `username` | string | IAM username to disable |

**Rollback**: Re-enables the login profile and reactivates the access keys that were active at execution time.

---

### `enable_iam_user`

Re-enables a previously disabled IAM user's console access and access keys.

| Parameter | Type | Description |
|---|---|---|
| `username` | string | IAM username to enable |

**Rollback**: Disables the user again.

---

### `rotate_iam_key`

Creates a new access key for the specified IAM user and deletes the oldest existing key.

| Parameter | Type | Description |
|---|---|---|
| `username` | string | IAM username whose key will be rotated |

**Rollback**: Deletes the newly created key and restores the deleted key. Note: AWS does not retain deleted key material — if the original key was used by a workload, that workload must be updated with the new key before rollback would be disruptive. Plan accordingly.

---

### `block_s3_public_access`

Enables all four public access block settings on an S3 bucket (`BlockPublicAcls`, `IgnorePublicAcls`, `BlockPublicPolicy`, `RestrictPublicBuckets`).

| Parameter | Type | Description |
|---|---|---|
| `bucket_name` | string | Name of the S3 bucket |

**Rollback**: Restores the previous public access block configuration captured at execution time.

---

### `attach_iam_policy`

Attaches a managed IAM policy to a user or role.

| Parameter | Type | Description |
|---|---|---|
| `principal_type` | enum: `user` \| `role` | Whether to attach to a user or role |
| `principal_name` | string | IAM user or role name |
| `policy_arn` | string | ARN of the managed policy to attach |

**Rollback**: Detaches the policy.

---

### `detach_iam_policy`

Detaches a managed IAM policy from a user or role.

| Parameter | Type | Description |
|---|---|---|
| `principal_type` | enum: `user` \| `role` | Whether to detach from a user or role |
| `principal_name` | string | IAM user or role name |
| `policy_arn` | string | ARN of the managed policy to detach |

**Rollback**: Re-attaches the policy.
