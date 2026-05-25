# Why We Test Every Security Action Against Real Infrastructure Before It Ships

*Draft — for nexplane.ai blog*

---

Here's a thing most security platforms don't tell you: their "rollback" button has never actually been tested against your production environment. It works in unit tests. It might work in a staging environment that doesn't quite match prod. But the rollback itself — the thing you'll reach for at 2am when something goes wrong — has never been run against the real systems it's supposed to undo.

We found this uncomfortable early in Nexplane's development and decided to fix it structurally.

## What We Actually Test

Every connector in Nexplane has a smoke test phase that runs against real infrastructure. Not mocks. Not fixtures. Real AWS accounts, real Active Directory instances, real Nessus scanners, real Linux hosts over real SSH connections.

The test follows the same path a security engineer would:

1. Create a Change Request through the Nexplane API
2. Plan it
3. Approve it
4. Execute it — watch it actually change something on the target system
5. Verify the change took effect
6. **Roll it back**
7. Verify the system is back to its prior state

That last step — rollback verification — is the one most platforms skip. It's also the most important one.

## The Rollback Guarantee

Nexplane's core promise is that every automated security action is reversible. Registry change, security group update, IAM key rotation, DNS record modification — all of it has a rollback path, and all of those rollback paths are tested against real infrastructure on every release.

This isn't just defensive engineering. It changes the risk calculation for the engineer authorizing the action. If you know the rollback is tested and works, you authorize faster. You sleep better. You don't spend the first 20 minutes after executing a change frantically watching dashboards.

## Why Real Infrastructure Matters

We tried mocked tests early on. They passed. Then we ran against real systems and found:

- AWS API rate limits that mocks don't simulate
- Race conditions in Active Directory group propagation that only appear at scale
- WinRM authentication edge cases that depend on actual domain policy
- LDAP bind behaviors that vary between vendor implementations

Mocks test your code's logic. Real infrastructure tests whether your code works in the world. For security operations — where the blast radius of a bug is "we just locked everyone out" — the difference matters.

## The AMI Cache Pattern

One practical challenge: some test infrastructure is expensive to spin up. A fresh FreeIPA directory server takes 8+ minutes to bootstrap. A domain controller with proper DNS delegation takes even longer.

Our solution: after the first successful bootstrap, we snapshot the instance as an AMI and cache the AMI ID in SSM Parameter Store. Subsequent test runs restore from snapshot in under 90 seconds. We get full isolation (each run gets a fresh copy) with near-instant startup.

```python
def _check_smoke_ami_cache(ssm_client, ec2_client, service, config_hash):
    """Return a cached AMI ID if available and still 'available' in EC2."""
    try:
        param = ssm_client.get_parameter(
            Name=f"/nexplane/smoke-amis/{service}/{config_hash[:8]}"
        )
        ami_id = param["Parameter"]["Value"]
        images = ec2_client.describe_images(ImageIds=[ami_id])["Images"]
        if images and images[0]["State"] == "available":
            return ami_id
    except Exception:
        pass
    return None
```

Same pattern works for Nessus, Vault, LDAP — any infrastructure that's slow to provision but stable to snapshot.

## What This Means for You

If you're evaluating security automation platforms, ask one question: *has the rollback been tested against production-equivalent infrastructure?*

If the answer is "we have unit tests" or "our staging environment covers it," the rollback hasn't really been tested. Unit tests don't catch the AWS API throttling. Staging doesn't have the same domain policy your prod Active Directory does.

Nexplane's smoke tests run in our CI/CD pipeline and against our own production-equivalent AWS environment. Every connector. Every rollback path. Every release.

The rollback works because we've run it. Many times.

---

*Nexplane is a security operations platform that automates security actions with guaranteed rollback. [Learn more](https://nexplane.ai) or [get started self-hosted](https://github.com/nexplane/nexplane).*
