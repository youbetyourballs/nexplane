# Release Pipeline & releases.nexplane.ai — Design Spec

**Date:** 2026-06-25
**Status:** Approved for planning

---

## Goal

Establish a fully automated release pipeline triggered by a single git tag on the `nexplane` repo. The pipeline builds versioned Docker images, packages a source bundle, and publishes `latest.json` to `releases.nexplane.ai` — the endpoint consumed by the platform upgrade CR version poller. Only the repository administrator can cut a release.

---

## Repository Responsibilities

| Repo | Role |
|------|------|
| `nexplane` | Core platform source; git tag here is the sole release trigger |
| `nexplane-deploy` | Receives cross-repo dispatch; runs `cut_release.py`; pushes to S3 |
| `nexplane-infra` (new) | Terraform for S3, CloudFront, ACM, IAM |

---

## Release Metadata File

A `release.yml` file at the root of `nexplane` is edited before each tag is pushed. It carries the human-authored fields that cannot be derived from code:

```yaml
# release.yml — edit before running: git tag v1.2.3 && git push --tags
severity: recommended        # recommended | security | critical
min_compatible_version: 1.0.0
changelog_url: https://docs.nexplane.ai/changelog/1.2.3
notes: "Brief human-readable release notes"
```

`severity` drives the banner colour in the admin UI (`recommended` = blue, `security` = amber, `critical` = red with dismissal blocked).

---

## Pipeline Flow

```
nexplane: git tag v1.2.3 && git push --tags
    │
    └─► .github/workflows/release.yml
            1. Build + push ghcr.io/nexplane/nexplane-backend:v1.2.3 + :latest
            2. Build + push ghcr.io/nexplane/nexplane-frontend:v1.2.3 + :latest
            3. git archive HEAD → nexplane-src-v1.2.3.tar.gz
            4. sha256 bundle
            5. Upload bundle → s3://nexplane-artifacts/releases/v1.2.3/
            6. repository_dispatch → nexplane-deploy
               payload: { version, sha256, severity, min_compatible_version,
                          changelog_url, notes }

nexplane-deploy: receives repository_dispatch (cut_release)
    │
    └─► .github/workflows/cut-release.yml
            1. Download bundle from s3://nexplane-artifacts/releases/v1.2.3/
            2. Verify sha256 matches dispatch payload
            3. python release/cut_release.py --version v1.2.3 --channel stable
            4. Write releases/latest.json (see schema below)
            5. Upload manifest.json + latest.json → s3://nexplane-releases/
            6. CloudFront invalidation: /latest.json
            7. Commit updated release/manifest.json → nexplane-deploy master
```

---

## latest.json Schema

Served at `https://releases.nexplane.ai/latest.json`. This is a stable thin projection — the version poller never needs to parse the full manifest.

```json
{
  "version": "1.2.3",
  "released_at": "2026-06-25T12:00:00Z",
  "min_compatible_version": "1.0.0",
  "image_tags": {
    "backend": "ghcr.io/nexplane/nexplane-backend:v1.2.3",
    "frontend": "ghcr.io/nexplane/nexplane-frontend:v1.2.3"
  },
  "bundle_sha256": "abc123...",
  "severity": "recommended",
  "changelog_url": "https://docs.nexplane.ai/changelog/1.2.3",
  "notes": "Brief human-readable release notes"
}
```

`manifest.json` is also published to S3 and served via CloudFront for the hosted ops instance, which needs the full release history.

---

## Infrastructure (nexplane-infra)

All managed via Terraform. No manual AWS console steps.

### S3 Buckets

**`nexplane-releases`**
- Serves `latest.json` and `manifest.json` via CloudFront
- Not directly public — all access via CloudFront origin access control
- Versioning enabled (allows recovery if a bad publish overwrites latest.json)

**`nexplane-artifacts`**
- Stores source bundles: `releases/v1.2.3/nexplane-src-v1.2.3.tar.gz`
- Private — only accessible by the `nexplane-ci` IAM user
- Lifecycle rule: expire objects older than 365 days

### CloudFront Distribution

- Origin: `nexplane-releases` S3 bucket (origin access control, not public bucket)
- Alternate domain: `releases.nexplane.ai`
- ACM certificate: `releases.nexplane.ai` (provisioned in us-east-1 — required for CloudFront)
- Cache behaviours:
  - `/latest.json` — TTL 300s (5 minutes); ensures version notifications are not stale
  - `/manifest.json` — TTL 3600s
  - `/releases/*` (bundles) — TTL 86400s; content is immutable per version

### IAM

A dedicated `nexplane-ci` IAM user with a minimal inline policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": [
        "arn:aws:s3:::nexplane-releases/*",
        "arn:aws:s3:::nexplane-artifacts/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "cloudfront:CreateInvalidation",
      "Resource": "arn:aws:cloudfront::<account-id>:distribution/<dist-id>"
    }
  ]
}
```

Access key stored as `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in GitHub secrets on both `nexplane` and `nexplane-deploy`.

### nexplane-infra Repo Structure

```
nexplane-infra/
  main.tf          — provider, backend (S3 state)
  s3.tf            — nexplane-releases + nexplane-artifacts buckets
  cloudfront.tf    — distribution + origin access control
  acm.tf           — certificate (us-east-1)
  iam.tf           — nexplane-ci user + policy
  outputs.tf       — cloudfront_domain (needed for GoDaddy CNAME)
  variables.tf
  terraform.tfvars
```

---

## DNS

`nexplane.ai` is registered and managed through GoDaddy. Terraform manages CloudFront and ACM; DNS requires one manual step.

> ⚠️ **ACTION REQUIRED — GoDaddy CNAME**
>
> After `terraform apply` completes, the CloudFront domain will be printed as an output (e.g. `d1abc123xyz.cloudfront.net`). Log in to GoDaddy and add:
>
> | Type | Name | Value | TTL |
> |------|------|-------|-----|
> | CNAME | `releases` | `<cloudfront_domain output>` | 600 |
>
> ACM certificate validation also requires a CNAME — Terraform will output the name/value pair for that too. Add it at the same time.
>
> This is a one-time step. Everything else is automated.

---

## Tag Protection

Configured in `nexplane` repo under Settings → Rules → New ruleset:

- **Name:** `release-tags`
- **Target:** Tags matching `v*.*.*`
- **Restriction:** Repository administrators only can create matching tags
- **Effect:** Contributors with write access cannot push a release tag; forks cannot trigger the pipeline (CI secrets are not inherited by forks)

The cross-repo dispatch uses a PAT (`DEPLOY_DISPATCH_TOKEN`) scoped to `nexplane-deploy:actions:write`, stored as a secret on `nexplane`. Even if a tag were pushed without authorisation, the dispatch would fail without this secret.

---

## Nightly Builds

`.github/workflows/nightly.yml` triggers on every push to `master` and on a daily schedule (02:00 UTC). It builds and pushes:

```
ghcr.io/nexplane/nexplane-backend:nightly
ghcr.io/nexplane/nexplane-frontend:nightly
```

Tags are overwritten on each run — `nightly` always points to the latest master commit. A `nightly-<YYYY-MM-DD>` dated tag is also pushed and retained for 30 days (pruned by a separate cleanup workflow), giving contributors a stable reference if they need to pin a specific day's build.

Nightly builds are explicitly **not** published to `releases.nexplane.ai` — the version poller only surfaces versioned releases to operators. Nightly is for contributors and internal testing only.

---

## Cutting a Release (Operator Runbook)

```bash
# 1. Edit release metadata
vim release.yml      # set severity, min_compatible_version, changelog_url, notes

# 2. Commit and push
git add release.yml
git commit -m "chore: prepare v1.2.3 release"
git push origin master

# 3. Tag and push — this triggers the full pipeline
git tag v1.2.3
git push origin v1.2.3

# 4. Monitor
# GitHub Actions → nexplane → release.yml
# GitHub Actions → nexplane-deploy → cut-release.yml
# https://releases.nexplane.ai/latest.json  (live within ~3 minutes of tag push)
```

This can also be run on your behalf: say "cut a release at v1.x.x" and it will be done.

---

## Files Affected

### nexplane

| File | Change |
|------|--------|
| `.github/workflows/release.yml` | New — tag-triggered release workflow |
| `.github/workflows/nightly.yml` | New — master-tracking nightly image build |
| `release.yml` | New — per-release metadata (severity, notes, etc.) |
| `.github/workflows/publish-images.yml` | Remove `push: branches: [master]` trigger — superseded by `nightly.yml` and `release.yml` |

### nexplane-deploy

| File | Change |
|------|--------|
| `.github/workflows/cut-release.yml` | New — `repository_dispatch` receiver |
| `releases/latest.json` | New — thin projection updated on each release |

### nexplane-infra (new repo)

| File | Change |
|------|--------|
| `main.tf`, `s3.tf`, `cloudfront.tf`, `acm.tf`, `iam.tf`, `outputs.tf` | New — full Terraform stack |

---

## Open Dependencies

- **`nexplane-infra` repo creation** — needs to be created under `youbetyourballs` before Terraform can be applied
- **GoDaddy CNAME** — one-time manual step after first `terraform apply`; exact values printed as Terraform outputs
- **`DEPLOY_DISPATCH_TOKEN` PAT** — needs to be created in GitHub (Settings → Developer settings → Personal access tokens) with `nexplane-deploy:actions:write` scope and stored as a secret on `nexplane`
- **AWS credentials** — `nexplane-ci` IAM user access key needs to be stored as `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in GitHub secrets on both repos
- **`publish-images.yml` change** — removing the `branches: [master]` trigger means images no longer publish on every commit; only on tags. Confirm this is acceptable before implementing.
