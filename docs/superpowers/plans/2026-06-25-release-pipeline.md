# Release Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a fully automated release pipeline triggered by a single git tag on `nexplane`, building versioned Docker images, packaging a source bundle, and publishing `latest.json` to `releases.nexplane.ai` for the platform upgrade CR version poller.

**Architecture:** A git tag `vX.Y.Z` on `nexplane` triggers `release.yml`, which builds images, creates a source bundle, uploads to S3, and fires a `repository_dispatch` to `nexplane-deploy`. `nexplane-deploy`'s `cut-release.yml` downloads the bundle, verifies the sha256, calls `cut_release.py` to update `manifest.json`, writes `releases/latest.json`, uploads both to S3, and invalidates CloudFront. A separate `nightly.yml` workflow pushes `:nightly` images on every master push and on a daily schedule. A new `nexplane-infra` repo holds the Terraform for S3, CloudFront, ACM, and IAM.

**Tech Stack:** GitHub Actions, Docker/GHCR, Python 3 (stdlib only), AWS S3, CloudFront, ACM, IAM, Terraform >= 1.6, AWS provider >= 5.0

## Global Constraints

- All S3 bucket access via CloudFront origin access control — buckets never public
- `nexplane-ci` IAM user holds only the exact permissions listed in the spec; no wildcards
- `cut_release.py` is existing code — do not modify it; adapt the workflow to its CLI (`cut` subcommand, `--bundle`, `--version`, `--channel`, `--notes`)
- `release.yml` (metadata file) is committed to `nexplane` root; its fields are `severity`, `min_compatible_version`, `changelog_url`, `notes`
- CloudFront alternate domain: `releases.nexplane.ai`; ACM cert must be in `us-east-1`
- Terraform state backend: S3 (bucket and key defined in `main.tf`)
- `nightly` tag is overwritten on each run; dated `nightly-YYYY-MM-DD` tags retained 30 days
- Do not modify `publish-images.yml` body — only remove the `push: branches: [master]` trigger block; `workflow_dispatch` trigger stays

---

## Scope

This plan covers four independent subsystems that must be implemented in order because later tasks depend on infrastructure that earlier tasks provision:

1. **Task 1** — `nexplane`: `release.yml` metadata file + `nightly.yml` workflow + retire master trigger from `publish-images.yml`
2. **Task 2** — `nexplane`: `release.yml` tag-triggered workflow (builds images, bundles, dispatches to deploy)
3. **Task 3** — `nexplane-deploy`: `cut-release.yml` workflow + `releases/latest.json` scaffold
4. **Task 4** — `nexplane-infra`: Full Terraform stack (S3, CloudFront, ACM, IAM)
5. **Task 5** — Manual steps: GitHub secrets, tag protection ruleset, GoDaddy DNS (documented, not automated)

Tasks 1–3 can be implemented before Task 4 (the workflows just need the infra to exist before a real tag is pushed). Task 4 is standalone Terraform work. Task 5 is purely manual and blocks a live end-to-end test.

---

## File Map

### nexplane repo (`f:\Nexplane\nexplane`)

| File | Action |
|------|--------|
| `release.yml` | Create — per-release metadata edited before each tag |
| `.github/workflows/nightly.yml` | Create — master push + daily schedule image build |
| `.github/workflows/release.yml` | Create — tag-triggered full release workflow |
| `.github/workflows/publish-images.yml` | Modify — remove `push: branches: [master]` trigger |

### nexplane-deploy repo (`f:\Nexplane\nexplane-deploy`)

| File | Action |
|------|--------|
| `.github/workflows/cut-release.yml` | Create — `repository_dispatch` receiver |
| `releases/latest.json` | Create — stub; overwritten on first real release |

### nexplane-infra repo (`f:\Nexplane\nexplane-infra` — new repo)

| File | Action |
|------|--------|
| `main.tf` | Create — provider config, S3 Terraform state backend |
| `variables.tf` | Create — `aws_region`, `account_id`, `cloudfront_distribution_id` |
| `terraform.tfvars` | Create — concrete variable values |
| `s3.tf` | Create — `nexplane-releases` and `nexplane-artifacts` buckets |
| `cloudfront.tf` | Create — distribution + origin access control |
| `acm.tf` | Create — ACM cert in `us-east-1` |
| `iam.tf` | Create — `nexplane-ci` IAM user + inline policy |
| `outputs.tf` | Create — `cloudfront_domain`, ACM validation CNAME name/value |

---

## Task 1: nexplane — Release metadata file, nightly workflow, retire master trigger

**Repos:** `nexplane`

**Files:**
- Create: `f:\Nexplane\nexplane\release.yml`
- Create: `f:\Nexplane\nexplane\.github\workflows\nightly.yml`
- Modify: `f:\Nexplane\nexplane\.github\workflows\publish-images.yml` (lines 4-6, remove `push: branches: [master]`)

**Interfaces:**
- Produces: `release.yml` at repo root with fields `severity`, `min_compatible_version`, `changelog_url`, `notes` — consumed by Task 2's `release.yml` workflow via `${{ steps.meta.outputs.* }}`

- [ ] **Step 1: Create `release.yml` metadata file**

```yaml
# release.yml — edit before running: git tag v1.2.3 && git push --tags
severity: recommended        # recommended | security | critical
min_compatible_version: 1.0.0
changelog_url: https://docs.nexplane.ai/changelog/1.0.0
notes: "Initial automated release"
```

Save to `f:\Nexplane\nexplane\release.yml`.

- [ ] **Step 2: Create `nightly.yml`**

Save to `f:\Nexplane\nexplane\.github\workflows\nightly.yml`:

```yaml
name: Nightly Images

on:
  push:
    branches: [master]
  schedule:
    - cron: '0 2 * * *'
  workflow_dispatch:

permissions:
  packages: write
  contents: read

jobs:
  build-backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Compute dated tag
        id: date
        run: echo "tag=nightly-$(date -u +%Y-%m-%d)" >> "$GITHUB_OUTPUT"

      - name: Build and push backend
        uses: docker/build-push-action@v5
        with:
          context: backend
          file: backend/Dockerfile
          push: true
          tags: |
            ghcr.io/nexplane/nexplane-backend:nightly
            ghcr.io/nexplane/nexplane-backend:${{ steps.date.outputs.tag }}

  build-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Compute dated tag
        id: date
        run: echo "tag=nightly-$(date -u +%Y-%m-%d)" >> "$GITHUB_OUTPUT"

      - name: Build and push frontend
        uses: docker/build-push-action@v5
        with:
          context: frontend
          file: frontend/Dockerfile
          push: true
          tags: |
            ghcr.io/nexplane/nexplane-frontend:nightly
            ghcr.io/nexplane/nexplane-frontend:${{ steps.date.outputs.tag }}
```

- [ ] **Step 3: Remove `push: branches: [master]` from `publish-images.yml`**

Current `on:` block (lines 3-6):
```yaml
on:
  push:
    branches: [master]
  workflow_dispatch:
```

Replace with:
```yaml
on:
  workflow_dispatch:
```

This makes `publish-images.yml` manual-only. `nightly.yml` now owns the master-push image builds.

- [ ] **Step 4: Verify the diff is correct**

```bash
cd f:/Nexplane/nexplane
git diff .github/workflows/publish-images.yml
```

Expected: only the `push: branches: [master]` block removed; rest of file unchanged.

- [ ] **Step 5: Commit**

```bash
cd f:/Nexplane/nexplane
git add release.yml .github/workflows/nightly.yml .github/workflows/publish-images.yml
git commit -m "feat: add release.yml metadata file, nightly workflow; retire master trigger from publish-images"
```

---

## Task 2: nexplane — Tag-triggered `release.yml` workflow

**Repos:** `nexplane`

**Files:**
- Create: `f:\Nexplane\nexplane\.github\workflows\release.yml`

**Interfaces:**
- Consumes: `release.yml` at repo root (parsed via `yq` in the workflow)
- Consumes: GitHub secrets `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (S3 upload), `DEPLOY_DISPATCH_TOKEN` (cross-repo dispatch)
- Produces: `repository_dispatch` event `cut_release` to `nexplane-deploy` with payload `{ version, sha256, severity, min_compatible_version, changelog_url, notes }`
- Produces: `s3://nexplane-artifacts/releases/vX.Y.Z/nexplane-src-vX.Y.Z.tar.gz`
- Produces: GHCR images `ghcr.io/nexplane/nexplane-backend:vX.Y.Z` and `:latest`, same for frontend

- [ ] **Step 1: Create `release.yml` workflow**

Save to `f:\Nexplane\nexplane\.github\workflows\release.yml`:

```yaml
name: Release

on:
  push:
    tags:
      - 'v*.*.*'

permissions:
  packages: write
  contents: read

env:
  REGISTRY: ghcr.io
  BACKEND_IMAGE: ghcr.io/nexplane/nexplane-backend
  FRONTEND_IMAGE: ghcr.io/nexplane/nexplane-frontend

jobs:
  build-and-release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Extract version from tag
        id: version
        run: |
          VERSION="${GITHUB_REF_NAME}"
          echo "version=${VERSION}" >> "$GITHUB_OUTPUT"
          # Strip leading 'v' for bare version number
          echo "bare=${VERSION#v}" >> "$GITHUB_OUTPUT"

      - name: Read release metadata
        id: meta
        run: |
          # yq is available on ubuntu-latest runners
          SEVERITY=$(yq '.severity' release.yml)
          MIN_VER=$(yq '.min_compatible_version' release.yml)
          CHANGELOG=$(yq '.changelog_url' release.yml)
          NOTES=$(yq '.notes' release.yml)
          echo "severity=${SEVERITY}" >> "$GITHUB_OUTPUT"
          echo "min_compatible_version=${MIN_VER}" >> "$GITHUB_OUTPUT"
          echo "changelog_url=${CHANGELOG}" >> "$GITHUB_OUTPUT"
          echo "notes=${NOTES}" >> "$GITHUB_OUTPUT"

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push backend
        uses: docker/build-push-action@v5
        with:
          context: backend
          file: backend/Dockerfile
          push: true
          tags: |
            ${{ env.BACKEND_IMAGE }}:${{ steps.version.outputs.version }}
            ${{ env.BACKEND_IMAGE }}:latest

      - name: Build and push frontend
        uses: docker/build-push-action@v5
        with:
          context: frontend
          file: frontend/Dockerfile
          push: true
          tags: |
            ${{ env.FRONTEND_IMAGE }}:${{ steps.version.outputs.version }}
            ${{ env.FRONTEND_IMAGE }}:latest

      - name: Create source bundle
        id: bundle
        run: |
          VERSION="${{ steps.version.outputs.version }}"
          BUNDLE="nexplane-src-${VERSION}.tar.gz"
          git archive HEAD --format=tar.gz -o "${BUNDLE}"
          SHA256=$(sha256sum "${BUNDLE}" | awk '{print $1}')
          echo "bundle=${BUNDLE}" >> "$GITHUB_OUTPUT"
          echo "sha256=${SHA256}" >> "$GITHUB_OUTPUT"

      - name: Upload bundle to S3
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          AWS_DEFAULT_REGION: us-east-1
        run: |
          VERSION="${{ steps.version.outputs.version }}"
          BUNDLE="${{ steps.bundle.outputs.bundle }}"
          aws s3 cp "${BUNDLE}" "s3://nexplane-artifacts/releases/${VERSION}/${BUNDLE}"

      - name: Dispatch to nexplane-deploy
        env:
          DEPLOY_DISPATCH_TOKEN: ${{ secrets.DEPLOY_DISPATCH_TOKEN }}
        run: |
          curl -f -X POST \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer ${DEPLOY_DISPATCH_TOKEN}" \
            -H "X-GitHub-Api-Version: 2022-11-28" \
            https://api.github.com/repos/youbetyourballs/nexplane-deploy/dispatches \
            -d "$(jq -n \
              --arg version "${{ steps.version.outputs.version }}" \
              --arg bare "${{ steps.version.outputs.bare }}" \
              --arg sha256 "${{ steps.bundle.outputs.sha256 }}" \
              --arg bundle "nexplane-src-${{ steps.version.outputs.version }}.tar.gz" \
              --arg severity "${{ steps.meta.outputs.severity }}" \
              --arg min_compatible_version "${{ steps.meta.outputs.min_compatible_version }}" \
              --arg changelog_url "${{ steps.meta.outputs.changelog_url }}" \
              --arg notes "${{ steps.meta.outputs.notes }}" \
              '{
                "event_type": "cut_release",
                "client_payload": {
                  "version": $version,
                  "bare_version": $bare,
                  "sha256": $sha256,
                  "bundle": $bundle,
                  "severity": $severity,
                  "min_compatible_version": $min_compatible_version,
                  "changelog_url": $changelog_url,
                  "notes": $notes
                }
              }')"
```

- [ ] **Step 2: Verify YAML syntax locally**

```bash
cd f:/Nexplane/nexplane
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```

Expected: no output (no syntax errors).

- [ ] **Step 3: Commit**

```bash
cd f:/Nexplane/nexplane
git add .github/workflows/release.yml
git commit -m "feat: add tag-triggered release workflow"
```

---

## Task 3: nexplane-deploy — `cut-release.yml` workflow and `releases/latest.json`

**Repos:** `nexplane-deploy`

**Files:**
- Create: `f:\Nexplane\nexplane-deploy\.github\workflows\cut-release.yml`
- Create: `f:\Nexplane\nexplane-deploy\releases\latest.json`

**Interfaces:**
- Consumes: `repository_dispatch` event `cut_release` with `client_payload.version`, `client_payload.bare_version`, `client_payload.sha256`, `client_payload.bundle`, `client_payload.severity`, `client_payload.min_compatible_version`, `client_payload.changelog_url`, `client_payload.notes`
- Consumes: Existing `release/cut_release.py cut` CLI: `--version`, `--bundle`, `--channel`, `--notes`
- Consumes: GitHub secrets `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (S3 download + upload), `CF_DISTRIBUTION_ID` (CloudFront invalidation)
- Produces: Updated `release/manifest.json` committed to `nexplane-deploy` master
- Produces: `releases/latest.json` committed to `nexplane-deploy` master
- Produces: `s3://nexplane-releases/latest.json` and `s3://nexplane-releases/manifest.json`

- [ ] **Step 1: Create `releases/` directory with stub `latest.json`**

Save to `f:\Nexplane\nexplane-deploy\releases\latest.json`:

```json
{
  "version": "0.0.0",
  "released_at": "1970-01-01T00:00:00Z",
  "min_compatible_version": "0.0.0",
  "image_tags": {
    "backend": "ghcr.io/nexplane/nexplane-backend:v0.0.0",
    "frontend": "ghcr.io/nexplane/nexplane-frontend:v0.0.0"
  },
  "bundle_sha256": "",
  "severity": "recommended",
  "changelog_url": "",
  "notes": "Stub — replaced on first release"
}
```

- [ ] **Step 2: Create `cut-release.yml`**

Save to `f:\Nexplane\nexplane-deploy\.github\workflows\cut-release.yml`:

```yaml
name: Cut Release

on:
  repository_dispatch:
    types: [cut_release]

permissions:
  contents: write

jobs:
  cut:
    runs-on: ubuntu-latest
    env:
      VERSION: ${{ github.event.client_payload.version }}
      BARE_VERSION: ${{ github.event.client_payload.bare_version }}
      SHA256: ${{ github.event.client_payload.sha256 }}
      BUNDLE: ${{ github.event.client_payload.bundle }}
      SEVERITY: ${{ github.event.client_payload.severity }}
      MIN_COMPAT: ${{ github.event.client_payload.min_compatible_version }}
      CHANGELOG_URL: ${{ github.event.client_payload.changelog_url }}
      NOTES: ${{ github.event.client_payload.notes }}
      AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
      AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
      AWS_DEFAULT_REGION: us-east-1
      CF_DISTRIBUTION_ID: ${{ secrets.CF_DISTRIBUTION_ID }}

    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Configure git
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"

      - name: Download source bundle from S3
        run: |
          mkdir -p artifacts
          aws s3 cp \
            "s3://nexplane-artifacts/releases/${VERSION}/${BUNDLE}" \
            "artifacts/${BUNDLE}"

      - name: Verify sha256
        run: |
          ACTUAL=$(sha256sum "artifacts/${BUNDLE}" | awk '{print $1}')
          if [ "${ACTUAL}" != "${SHA256}" ]; then
            echo "sha256 mismatch: expected ${SHA256}, got ${ACTUAL}" >&2
            exit 1
          fi
          echo "sha256 verified: ${ACTUAL}"

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Run cut_release.py
        run: |
          python release/cut_release.py cut \
            --version "${VERSION}" \
            --bundle "artifacts/${BUNDLE}" \
            --channel stable \
            --notes "${NOTES}"

      - name: Write releases/latest.json
        run: |
          RELEASED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
          python3 - <<'EOF'
          import json, os, datetime
          latest = {
            "version": os.environ["BARE_VERSION"],
            "released_at": os.environ["RELEASED_AT"],
            "min_compatible_version": os.environ["MIN_COMPAT"],
            "image_tags": {
              "backend": f"ghcr.io/nexplane/nexplane-backend:{os.environ['VERSION']}",
              "frontend": f"ghcr.io/nexplane/nexplane-frontend:{os.environ['VERSION']}"
            },
            "bundle_sha256": os.environ["SHA256"],
            "severity": os.environ["SEVERITY"],
            "changelog_url": os.environ["CHANGELOG_URL"],
            "notes": os.environ["NOTES"]
          }
          with open("releases/latest.json", "w") as f:
            json.dump(latest, f, indent=2)
            f.write("\n")
          EOF
        env:
          RELEASED_AT: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

      - name: Upload to S3
        run: |
          aws s3 cp releases/latest.json s3://nexplane-releases/latest.json \
            --content-type application/json
          aws s3 cp release/manifest.json s3://nexplane-releases/manifest.json \
            --content-type application/json

      - name: Invalidate CloudFront
        run: |
          aws cloudfront create-invalidation \
            --distribution-id "${CF_DISTRIBUTION_ID}" \
            --paths "/latest.json"

      - name: Commit updated manifest and latest.json
        run: |
          git add release/manifest.json releases/latest.json
          git commit -m "chore: release ${VERSION}"
          git push origin master
```

> **Note on the Python heredoc env var:** The `RELEASED_AT` env var assignment in `env:` is a bash expression that won't expand in the `env:` block. Replace the `Write releases/latest.json` step's env block:

```yaml
      - name: Write releases/latest.json
        run: |
          RELEASED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
          python3 -c "
          import json, os
          latest = {
            'version': os.environ['BARE_VERSION'],
            'released_at': os.environ['RELEASED_AT'],
            'min_compatible_version': os.environ['MIN_COMPAT'],
            'image_tags': {
              'backend': 'ghcr.io/nexplane/nexplane-backend:' + os.environ['VERSION'],
              'frontend': 'ghcr.io/nexplane/nexplane-frontend:' + os.environ['VERSION']
            },
            'bundle_sha256': os.environ['SHA256'],
            'severity': os.environ['SEVERITY'],
            'changelog_url': os.environ['CHANGELOG_URL'],
            'notes': os.environ['NOTES']
          }
          with open('releases/latest.json', 'w') as f:
            json.dump(latest, f, indent=2)
            f.write('\n')
          "
        env:
          RELEASED_AT: ${{ env.RELEASED_AT }}
```

This still won't work because `RELEASED_AT` is set mid-step. Use a single run step that sets and uses the variable in the same shell:

```yaml
      - name: Write releases/latest.json
        run: |
          RELEASED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
          python3 - <<PYEOF
          import json, os
          latest = {
            "version": os.environ["BARE_VERSION"],
            "released_at": os.environ["RELEASED_AT"],
            "min_compatible_version": os.environ["MIN_COMPAT"],
            "image_tags": {
              "backend": "ghcr.io/nexplane/nexplane-backend:" + os.environ["VERSION"],
              "frontend": "ghcr.io/nexplane/nexplane-frontend:" + os.environ["VERSION"]
            },
            "bundle_sha256": os.environ["SHA256"],
            "severity": os.environ["SEVERITY"],
            "changelog_url": os.environ["CHANGELOG_URL"],
            "notes": os.environ["NOTES"]
          }
          with open("releases/latest.json", "w") as f:
            json.dump(latest, f, indent=2)
            f.write("\n")
          PYEOF
```

The `RELEASED_AT` variable is exported to the Python subprocess via the shell's environment, using `export RELEASED_AT` before the heredoc:

```yaml
      - name: Write releases/latest.json
        run: |
          export RELEASED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
          python3 - <<'PYEOF'
          import json, os
          latest = {
            "version": os.environ["BARE_VERSION"],
            "released_at": os.environ["RELEASED_AT"],
            "min_compatible_version": os.environ["MIN_COMPAT"],
            "image_tags": {
              "backend": "ghcr.io/nexplane/nexplane-backend:" + os.environ["VERSION"],
              "frontend": "ghcr.io/nexplane/nexplane-frontend:" + os.environ["VERSION"]
            },
            "bundle_sha256": os.environ["SHA256"],
            "severity": os.environ["SEVERITY"],
            "changelog_url": os.environ["CHANGELOG_URL"],
            "notes": os.environ["NOTES"]
          }
          with open("releases/latest.json", "w") as f:
            json.dump(latest, f, indent=2)
            f.write("\n")
          PYEOF
```

Use this final version of the `Write releases/latest.json` step in the workflow file.

- [ ] **Step 3: Verify YAML syntax**

```bash
cd f:/Nexplane/nexplane-deploy
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/cut-release.yml'))"
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-deploy
git add .github/workflows/cut-release.yml releases/latest.json
git commit -m "feat: add cut-release workflow and releases/latest.json scaffold"
git push origin master
```

---

## Task 4: nexplane-infra — Full Terraform stack

**Repos:** `nexplane-infra` (new repo at `f:\Nexplane\nexplane-infra`)

**Files:**
- Create: `main.tf`, `variables.tf`, `terraform.tfvars`, `s3.tf`, `cloudfront.tf`, `acm.tf`, `iam.tf`, `outputs.tf`

**Interfaces:**
- Produces: `cloudfront_distribution_id` output (needed for `CF_DISTRIBUTION_ID` GitHub secret in Task 5)
- Produces: `cloudfront_domain` output (needed for GoDaddy CNAME in Task 5)
- Produces: `acm_validation_cname_name` and `acm_validation_cname_value` outputs (needed for GoDaddy DNS validation in Task 5)
- Produces: `nexplane-ci` IAM user (access key generated manually after apply; stored as GitHub secrets in Task 5)

**Prerequisite:** Create the GitHub repo `youbetyourballs/nexplane-infra` (private) and `git init` it before running `terraform init`. Also create the Terraform state bucket `nexplane-tf-state` in S3 manually (the state backend cannot manage itself).

- [ ] **Step 1: Create the repo directory and git init**

```bash
mkdir -p f:/Nexplane/nexplane-infra
cd f:/Nexplane/nexplane-infra
git init
```

- [ ] **Step 2: Create `main.tf`**

Save to `f:\Nexplane\nexplane-infra\main.tf`:

```hcl
terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }

  backend "s3" {
    bucket = "nexplane-tf-state"
    key    = "nexplane-infra/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region
}

# ACM must be in us-east-1 for CloudFront
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}
```

- [ ] **Step 3: Create `variables.tf`**

Save to `f:\Nexplane\nexplane-infra\variables.tf`:

```hcl
variable "aws_region" {
  description = "Primary AWS region for S3 and IAM"
  type        = string
  default     = "us-east-1"
}

variable "account_id" {
  description = "AWS account ID — used in IAM policy ARNs"
  type        = string
}
```

- [ ] **Step 4: Create `terraform.tfvars`**

Save to `f:\Nexplane\nexplane-infra\terraform.tfvars`:

```hcl
aws_region = "us-east-1"
account_id = "123456789012"  # Replace with actual AWS account ID
```

Replace `123456789012` with the real account ID before running `terraform apply`.

- [ ] **Step 5: Create `s3.tf`**

Save to `f:\Nexplane\nexplane-infra\s3.tf`:

```hcl
# nexplane-releases: serves latest.json and manifest.json via CloudFront
resource "aws_s3_bucket" "releases" {
  bucket = "nexplane-releases"
}

resource "aws_s3_bucket_versioning" "releases" {
  bucket = aws_s3_bucket.releases.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "releases" {
  bucket                  = aws_s3_bucket.releases.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Bucket policy: allow CloudFront OAC to read
resource "aws_s3_bucket_policy" "releases" {
  bucket = aws_s3_bucket.releases.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = {
        Service = "cloudfront.amazonaws.com"
      }
      Action   = "s3:GetObject"
      Resource = "${aws_s3_bucket.releases.arn}/*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = aws_cloudfront_distribution.releases.arn
        }
      }
    }]
  })
}

# nexplane-artifacts: stores source bundles; private, CI-only
resource "aws_s3_bucket" "artifacts" {
  bucket = "nexplane-artifacts"
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-old-releases"
    status = "Enabled"

    filter {
      prefix = "releases/"
    }

    expiration {
      days = 365
    }
  }
}
```

- [ ] **Step 6: Create `acm.tf`**

Save to `f:\Nexplane\nexplane-infra\acm.tf`:

```hcl
resource "aws_acm_certificate" "releases" {
  provider          = aws.us_east_1
  domain_name       = "releases.nexplane.ai"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# Expose validation records as outputs; actual DNS record is added manually in GoDaddy
```

- [ ] **Step 7: Create `cloudfront.tf`**

Save to `f:\Nexplane\nexplane-infra\cloudfront.tf`:

```hcl
resource "aws_cloudfront_origin_access_control" "releases" {
  name                              = "nexplane-releases-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "releases" {
  enabled             = true
  comment             = "nexplane releases CDN"
  aliases             = ["releases.nexplane.ai"]
  default_root_object = ""

  origin {
    domain_name              = aws_s3_bucket.releases.bucket_regional_domain_name
    origin_id                = "nexplane-releases-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.releases.id
  }

  # /latest.json — 5 minute TTL
  ordered_cache_behavior {
    path_pattern           = "/latest.json"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "nexplane-releases-s3"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
    min_ttl                = 0
    default_ttl            = 300
    max_ttl                = 300

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
  }

  # /manifest.json — 1 hour TTL
  ordered_cache_behavior {
    path_pattern           = "/manifest.json"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "nexplane-releases-s3"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
    min_ttl                = 0
    default_ttl            = 3600
    max_ttl                = 3600

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
  }

  # /releases/* (bundles) — 24 hour TTL; content is immutable per version
  ordered_cache_behavior {
    path_pattern           = "/releases/*"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "nexplane-releases-s3"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
    min_ttl                = 86400
    default_ttl            = 86400
    max_ttl                = 86400

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
  }

  # Default behaviour for any other paths
  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "nexplane-releases-s3"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
    min_ttl                = 0
    default_ttl            = 300
    max_ttl                = 86400

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate.releases.arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}
```

- [ ] **Step 8: Create `iam.tf`**

Save to `f:\Nexplane\nexplane-infra\iam.tf`:

```hcl
resource "aws_iam_user" "nexplane_ci" {
  name = "nexplane-ci"
  path = "/ci/"
}

resource "aws_iam_user_policy" "nexplane_ci" {
  name = "nexplane-ci-policy"
  user = aws_iam_user.nexplane_ci.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.releases.arn}/*",
          "${aws_s3_bucket.artifacts.arn}/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = "cloudfront:CreateInvalidation"
        Resource = "arn:aws:cloudfront::${var.account_id}:distribution/${aws_cloudfront_distribution.releases.id}"
      }
    ]
  })
}
```

- [ ] **Step 9: Create `outputs.tf`**

Save to `f:\Nexplane\nexplane-infra\outputs.tf`:

```hcl
output "cloudfront_domain" {
  description = "CloudFront domain to use as GoDaddy CNAME value for releases.nexplane.ai"
  value       = aws_cloudfront_distribution.releases.domain_name
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID — store as CF_DISTRIBUTION_ID GitHub secret"
  value       = aws_cloudfront_distribution.releases.id
}

output "acm_validation_cname_name" {
  description = "ACM DNS validation CNAME name — add to GoDaddy"
  value       = tolist(aws_acm_certificate.releases.domain_validation_options)[0].resource_record_name
}

output "acm_validation_cname_value" {
  description = "ACM DNS validation CNAME value — add to GoDaddy"
  value       = tolist(aws_acm_certificate.releases.domain_validation_options)[0].resource_record_value
}

output "nexplane_ci_iam_user_arn" {
  description = "ARN of the nexplane-ci IAM user"
  value       = aws_iam_user.nexplane_ci.arn
}
```

- [ ] **Step 10: Create `.gitignore`**

Save to `f:\Nexplane\nexplane-infra\.gitignore`:

```
.terraform/
*.tfstate
*.tfstate.backup
.terraform.lock.hcl
terraform.tfvars.local
```

- [ ] **Step 11: Validate Terraform locally**

Prerequisite: AWS CLI configured with credentials that have admin access. Terraform installed.

```bash
cd f:/Nexplane/nexplane-infra
terraform init
terraform validate
```

Expected output from `validate`: `Success! The configuration is valid.`

Note: `terraform plan` will fail until the `nexplane-tf-state` S3 bucket exists and the `account_id` in `terraform.tfvars` is correct. Fix those before running plan.

- [ ] **Step 12: Commit**

```bash
cd f:/Nexplane/nexplane-infra
git add .
git commit -m "feat: initial Terraform stack for nexplane-infra (S3, CloudFront, ACM, IAM)"
```

---

## Task 5: Manual Steps (document only — no code)

These steps cannot be automated and must be performed by a human with the right access. Do them in this order after Task 4's `terraform apply` completes successfully.

### 5a. Create `nexplane-tf-state` S3 bucket (before `terraform apply`)

This bucket stores Terraform state. It must exist before `terraform init` can use the S3 backend.

1. Log in to AWS console (or use `aws s3 mb s3://nexplane-tf-state --region us-east-1`)
2. Enable versioning on the bucket
3. Block all public access

### 5b. Create `nexplane-infra` GitHub repo

1. Go to https://github.com/new
2. Owner: `youbetyourballs`, Name: `nexplane-infra`, Private
3. Do not initialize with README
4. Push the local repo: `git remote add origin git@github.com:youbetyourballs/nexplane-infra.git && git push -u origin master`

### 5c. Run `terraform apply`

```bash
cd f:/Nexplane/nexplane-infra
# Edit terraform.tfvars: set account_id to real AWS account ID
terraform init
terraform plan
terraform apply
```

Note the outputs:
- `cloudfront_domain` — needed for GoDaddy CNAME
- `cloudfront_distribution_id` — needed for GitHub secret `CF_DISTRIBUTION_ID`
- `acm_validation_cname_name` and `acm_validation_cname_value` — needed for GoDaddy

### 5d. Create `nexplane-ci` IAM access key

After `terraform apply` creates the IAM user:

1. AWS Console → IAM → Users → `nexplane-ci` → Security credentials → Create access key
2. Select "Other" use case
3. Save the Access Key ID and Secret Access Key — they are shown once only

### 5e. Store GitHub Secrets

**On `nexplane` repo** (Settings → Secrets and variables → Actions → New repository secret):

| Secret name | Value |
|-------------|-------|
| `AWS_ACCESS_KEY_ID` | `nexplane-ci` IAM user access key ID |
| `AWS_SECRET_ACCESS_KEY` | `nexplane-ci` IAM user secret access key |
| `DEPLOY_DISPATCH_TOKEN` | PAT with `nexplane-deploy:actions:write` scope (see 5f) |

**On `nexplane-deploy` repo** (same path):

| Secret name | Value |
|-------------|-------|
| `AWS_ACCESS_KEY_ID` | `nexplane-ci` IAM user access key ID |
| `AWS_SECRET_ACCESS_KEY` | `nexplane-ci` IAM user secret access key |
| `CF_DISTRIBUTION_ID` | `cloudfront_distribution_id` output from Terraform |

### 5f. Create `DEPLOY_DISPATCH_TOKEN` PAT

1. GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token
2. Resource owner: `youbetyourballs`
3. Repository access: Only `nexplane-deploy`
4. Repository permissions: Actions = Read and write
5. Store as `DEPLOY_DISPATCH_TOKEN` secret on `nexplane` repo (see 5e)

### 5g. Configure tag protection ruleset on `nexplane`

1. `nexplane` repo → Settings → Rules → Rulesets → New ruleset
2. Name: `release-tags`
3. Enforcement status: Active
4. Target: Tags → Add tag pattern → `v*.*.*`
5. Rules: Check "Restrict creations"
6. Bypass list: Add `Repository admin` role
7. Save

### 5h. Add GoDaddy DNS records

After Terraform outputs are available:

1. Log in to GoDaddy → DNS for `nexplane.ai`
2. Add CNAME: Name = `releases`, Value = `<cloudfront_domain output>`, TTL = 600
3. Add CNAME for ACM validation: Name = `<acm_validation_cname_name>`, Value = `<acm_validation_cname_value>`, TTL = 600

Wait for ACM certificate to reach `Issued` state (can take 5-30 minutes after DNS propagates). Check in AWS Console → Certificate Manager.

### 5i. End-to-end smoke test

Once DNS is live and secrets are configured:

```bash
cd f:/Nexplane/nexplane
# Edit release.yml — set real version details
vim release.yml
git add release.yml
git commit -m "chore: prepare v1.0.0 release"
git push origin master

git tag v1.0.0
git push origin v1.0.0
```

Monitor:
- GitHub Actions → `nexplane` → `release.yml` run
- GitHub Actions → `nexplane-deploy` → `cut-release.yml` run (triggered automatically)
- `curl https://releases.nexplane.ai/latest.json` — should return the new version within ~3 minutes of the tag push

---

## Self-Review Checklist

### Spec coverage

| Spec requirement | Task |
|-----------------|------|
| `release.yml` metadata file at repo root | Task 1 |
| `nightly.yml` on master push + daily schedule | Task 1 |
| Retire `publish-images.yml` master trigger | Task 1 |
| `release.yml` tag-triggered workflow | Task 2 |
| Build backend + frontend images, push `:vX.Y.Z` + `:latest` | Task 2 |
| `git archive` source bundle | Task 2 |
| sha256 bundle | Task 2 |
| Upload bundle to `nexplane-artifacts` S3 | Task 2 |
| `repository_dispatch` to `nexplane-deploy` with full payload | Task 2 |
| `cut-release.yml` `repository_dispatch` receiver | Task 3 |
| Download + verify sha256 | Task 3 |
| Run `cut_release.py` | Task 3 |
| Write `releases/latest.json` | Task 3 |
| Upload `latest.json` + `manifest.json` to `nexplane-releases` S3 | Task 3 |
| CloudFront invalidation `/latest.json` | Task 3 |
| Commit updated `manifest.json` + `latest.json` to `nexplane-deploy` | Task 3 |
| `nexplane-releases` bucket (versioning, private, CloudFront OAC) | Task 4 |
| `nexplane-artifacts` bucket (private, 365-day lifecycle) | Task 4 |
| CloudFront distribution with per-path TTLs | Task 4 |
| ACM cert in `us-east-1` | Task 4 |
| `nexplane-ci` IAM user + minimal inline policy | Task 4 |
| Terraform state backend (S3) | Task 4 |
| `outputs.tf` (CloudFront domain, ACM validation CNAMEs) | Task 4 |
| GitHub secrets (all 5 secrets × 2 repos) | Task 5e |
| Tag protection ruleset `release-tags` | Task 5g |
| GoDaddy CNAME | Task 5h |
| `DEPLOY_DISPATCH_TOKEN` PAT creation | Task 5f |
| `nexplane-infra` repo creation | Task 5b |
| Nightly dated tag `nightly-YYYY-MM-DD` | Task 1, nightly.yml |
| `latest.json` schema matches spec | Task 3 |
| `manifest.json` served from S3/CloudFront | Task 3 |

All spec requirements are covered.

### Type/name consistency

- `cut_release.py cut` subcommand used in Task 3 ✓ (matches existing CLI)
- `sha256_file` from `executors/common/release.py` — not called in workflows (workflows use shell `sha256sum`) ✓
- `repository_dispatch` event type `cut_release` consistent between Task 2 (`event_type`) and Task 3 (`types: [cut_release]`) ✓
- S3 bucket names: `nexplane-releases` and `nexplane-artifacts` consistent across Tasks 2, 3, 4 ✓
- Image names: `ghcr.io/nexplane/nexplane-backend` and `ghcr.io/nexplane/nexplane-frontend` consistent across Tasks 1, 2, 3 ✓
- `CF_DISTRIBUTION_ID` secret name consistent between Task 3 (workflow env var) and Task 5e (secret storage) ✓
