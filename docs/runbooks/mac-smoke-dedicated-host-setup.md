# macOS Smoke Dedicated Host Setup

One-time setup to enable the MAC_AGENT_BOOTSTRAP smoke phase.

## Prerequisites

- AWS CLI configured with access to the Nexplane AWS account
- Nexplane backend running (for agent registration)

## Step 1: Allocate mac2.metal Dedicated Host

```bash
aws ec2 allocate-hosts \
  --instance-type mac2.metal \
  --availability-zone us-east-1a \
  --quantity 1 \
  --auto-placement on \
  --region us-east-1
```

Note the `HostId` (e.g. `h-0abc123def456789`). Store it:

```bash
aws ssm put-parameter \
  --name /nexplane/smoke/mac/dedicated-host-id \
  --value h-0abc123def456789 \
  --type String \
  --overwrite
```

## Step 2: Launch mac2.metal instance on the host

```bash
AMI=$(aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=amzn-ec2-macos-*" "Name=architecture,Values=arm64_mac" \
  --query 'reverse(sort_by(Images, &CreationDate))[0].ImageId' \
  --output text)

INSTANCE=$(aws ec2 run-instances \
  --image-id $AMI \
  --instance-type mac2.metal \
  --placement "HostId=h-0abc123def456789" \
  --key-name your-key-pair \
  --security-group-ids sg-xxxxxxxx \
  --region us-east-1 \
  --query 'Instances[0].InstanceId' \
  --output text)

echo "Instance: $INSTANCE"
```

Wait ~15 minutes for the instance to boot (mac2.metal has a slow first boot).

## Step 3: Install Nexplane agent

```bash
PUBLIC_IP=$(aws ec2 describe-instances --instance-ids $INSTANCE \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

VERSION=$(curl -sf https://nexplane-agent-downloads.s3.amazonaws.com/version-darwin-arm64)
curl -sf -o /tmp/nexplane-agent "https://nexplane-agent-downloads.s3.amazonaws.com/nexplane-agent-darwin-arm64-$VERSION"

scp -i ~/.ssh/your-key.pem /tmp/nexplane-agent ec2-user@$PUBLIC_IP:~/nexplane-agent
ssh -i ~/.ssh/your-key.pem ec2-user@$PUBLIC_IP \
  "chmod +x ~/nexplane-agent && sudo ~/nexplane-agent install \
   --backend-url https://your-nexplane-instance --token your-token"
```

## Step 4: Snapshot as AMI

```bash
AMI_ID=$(aws ec2 create-image \
  --instance-id $INSTANCE \
  --name "nexplane-smoke-mac-arm64-$(date +%Y%m%d)" \
  --no-reboot \
  --query 'ImageId' --output text)

aws ec2 wait image-available --image-ids $AMI_ID

aws ssm put-parameter \
  --name /nexplane/smoke-amis/mac/arm64/base \
  --value $AMI_ID \
  --type String \
  --overwrite
```

## Step 5: Run MAC_AGENT_BOOTSTRAP to verify

```bash
python tests/smoke/test_aws_live.py \
  --phases MAC_AGENT_BOOTSTRAP \
  --dedicated-host-id h-0abc123def456789
```

## Cost notes

- Dedicated Host: ~$0.906/hr ($21.74/day) ongoing
- Run MAC_AGENT_BOOTSTRAP nightly, not per-commit
- Terminate instances after each run; keep the host allocated
