# Nexplane Deploy Sub-project 1: Repo Skeleton + Bootstrap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the `nexplane-deploy` private GitHub repo with its full directory structure, extend the `nexplane` backend to load a commercial CR catalog from a mounted path, and provide Terraform to provision the ops Nexplane instance.

**Architecture:** `nexplane-deploy` is a private commercial overlay repo. Its `catalog/` and `executors/` directories are mounted into a standard `nexplane` backend container via a Docker volume. Two new env vars (`NEXPLANE_EDITION=commercial`, `NEXPLANE_COMMERCIAL_CATALOG_PATH`) activate commercial features and tell the catalog service where to find the extra catalog JSON files. Executor modules in the commercial path are loaded dynamically via `importlib.util.spec_from_file_location` rather than the standard import path, keeping the commercial code fully outside the `nexplane` package.

**Tech Stack:** Python 3.12, pydantic-settings, Terraform ~> 5.0 (AWS provider), Docker Compose, bash, AWS EC2/IAM/SSM

---

## File Map

**In `nexplane` repo (existing, modified):**
- Modify: `backend/app/config.py` — add `NEXPLANE_EDITION` and `NEXPLANE_COMMERCIAL_CATALOG_PATH` settings
- Modify: `backend/app/connectors/catalog_service.py` — extend `ActionCatalogService.__init__`, `_load`, `init_catalog_service`, `get_executor` to support commercial path
- Modify: `backend/app/main.py` — pass commercial catalog path to `init_catalog_service`
- Create: `backend/tests/unit/test_catalog_service_commercial.py` — tests for commercial catalog loading

**In `nexplane-deploy` repo (new):**
- Create: `.gitignore`
- Create: `README.md`
- Create: `catalog/.gitkeep` — populated in Sub-project 2
- Create: `executors/.gitkeep` — populated in Sub-project 2
- Create: `demo-seeds/.gitkeep` — populated in Sub-project 2
- Create: `smoke/.gitkeep` — populated in Sub-project 2
- Create: `artifacts/.gitkeep` — populated in Sub-project 5
- Create: `client-registry/_template.yaml` — schema for a client record
- Create: `client-registry/README.md`
- Create: `bootstrap/docker-compose.ops.yml` — extends `docker-compose.prod.yml` with commercial volume mount
- Create: `bootstrap/.env.ops.template` — env var template for the ops instance
- Create: `bootstrap/main.tf` — EC2, security group, IAM instance profile
- Create: `bootstrap/variables.tf`
- Create: `bootstrap/outputs.tf`
- Create: `bootstrap/scripts/install.sh` — Docker install, clone repos, start ops stack

---

## Task 1: Extend nexplane backend config

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_catalog_service_commercial.py
import os
import pytest
from app.config import Settings


def test_nexplane_edition_defaults_to_core():
    s = Settings()
    assert s.NEXPLANE_EDITION == "core"


def test_nexplane_edition_reads_from_env(monkeypatch):
    monkeypatch.setenv("NEXPLANE_EDITION", "commercial")
    s = Settings()
    assert s.NEXPLANE_EDITION == "commercial"


def test_commercial_catalog_path_defaults_to_none():
    s = Settings()
    assert s.NEXPLANE_COMMERCIAL_CATALOG_PATH is None


def test_commercial_catalog_path_reads_from_env(monkeypatch):
    monkeypatch.setenv("NEXPLANE_COMMERCIAL_CATALOG_PATH", "/mnt/commercial/catalog")
    s = Settings()
    assert s.NEXPLANE_COMMERCIAL_CATALOG_PATH == "/mnt/commercial/catalog"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend
pytest tests/unit/test_catalog_service_commercial.py -v
```

Expected: FAIL — `Settings` has no attribute `NEXPLANE_EDITION`

- [ ] **Step 3: Add the two new settings to config.py**

In `backend/app/config.py`, add these two fields to the `Settings` class after `WEBHOOK_SECRET`:

```python
    NEXPLANE_EDITION: str = "core"
    NEXPLANE_COMMERCIAL_CATALOG_PATH: str | None = None
```

Full updated `Settings` class fields (insert after `WEBHOOK_SECRET: str = "changeme"`):

```python
    NEXPLANE_EDITION: str = "core"
    NEXPLANE_COMMERCIAL_CATALOG_PATH: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend
pytest tests/unit/test_catalog_service_commercial.py::test_nexplane_edition_defaults_to_core tests/unit/test_catalog_service_commercial.py::test_commercial_catalog_path_defaults_to_none -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/unit/test_catalog_service_commercial.py
git commit -m "feat: add NEXPLANE_EDITION and NEXPLANE_COMMERCIAL_CATALOG_PATH settings"
```

---

## Task 2: Extend catalog_service.py to load commercial catalog JSONs

**Files:**
- Modify: `backend/app/connectors/catalog_service.py`

The current signature is `ActionCatalogService(catalog_dir)`. We extend it to accept an optional `commercial_catalog_dir`. If set and the directory exists, its JSON files are loaded after the core catalog. Commercial entries with the same `connector_type` as a core entry extend (merge actions) rather than replace.

- [ ] **Step 1: Write the failing tests** (append to existing test file)

```python
# Add to backend/tests/unit/test_catalog_service_commercial.py
import json
import pathlib
import tempfile
from app.connectors.catalog_service import ActionCatalogService


def _make_catalog(tmpdir: str, connector_type: str, action_id: str, executor: str) -> pathlib.Path:
    d = pathlib.Path(tmpdir)
    (d / f"{connector_type}.json").write_text(json.dumps({
        "connector_type": connector_type,
        "display_name": connector_type.title(),
        "actions": [{
            "action_id": action_id,
            "generic_action": action_id,
            "action_type": "change",
            "display_name": action_id,
            "executor": executor,
        }]
    }))
    return d


def test_commercial_catalog_loaded_alongside_core():
    with tempfile.TemporaryDirectory() as core_dir, \
         tempfile.TemporaryDirectory() as comm_dir:
        _make_catalog(core_dir, "aws", "list_instances", "aws.list_instances")
        _make_catalog(comm_dir, "ops", "provision_instance", "commercial.provision_instance")

        svc = ActionCatalogService(
            pathlib.Path(core_dir),
            commercial_catalog_dir=pathlib.Path(comm_dir),
        )
        types = svc.list_connector_types()
        assert "aws" in types
        assert "ops" in types


def test_commercial_catalog_dir_missing_is_ignored():
    with tempfile.TemporaryDirectory() as core_dir:
        _make_catalog(core_dir, "aws", "list_instances", "aws.list_instances")
        svc = ActionCatalogService(
            pathlib.Path(core_dir),
            commercial_catalog_dir=pathlib.Path("/nonexistent/path"),
        )
        assert "aws" in svc.list_connector_types()


def test_commercial_actions_accessible_via_get_action_def():
    with tempfile.TemporaryDirectory() as core_dir, \
         tempfile.TemporaryDirectory() as comm_dir:
        _make_catalog(core_dir, "aws", "list_instances", "aws.list_instances")
        _make_catalog(comm_dir, "ops", "provision_instance", "commercial.provision_instance")

        svc = ActionCatalogService(
            pathlib.Path(core_dir),
            commercial_catalog_dir=pathlib.Path(comm_dir),
        )
        action = svc.get_action_def("ops", "provision_instance")
        assert action["executor"] == "commercial.provision_instance"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend
pytest tests/unit/test_catalog_service_commercial.py::test_commercial_catalog_loaded_alongside_core -v
```

Expected: FAIL — `ActionCatalogService.__init__` doesn't accept `commercial_catalog_dir`

- [ ] **Step 3: Extend ActionCatalogService.__init__ and init_catalog_service**

In `backend/app/connectors/catalog_service.py`, update `__init__` and `init_catalog_service`:

```python
class ActionCatalogService:
    def __init__(
        self,
        catalog_dir: pathlib.Path,
        commercial_catalog_dir: pathlib.Path | None = None,
    ):
        self._catalog: dict[str, list[dict]] = {}
        self._raw: dict[str, dict] = {}
        self._generic_index: dict[str, list[ActionOption]] = {}
        self._commercial_dir: pathlib.Path | None = None
        self._load(catalog_dir)
        if commercial_catalog_dir and commercial_catalog_dir.is_dir():
            self._commercial_dir = commercial_catalog_dir
            self._load(commercial_catalog_dir)
```

Also update `init_catalog_service` (near line 102):

```python
def init_catalog_service(
    catalog_dir: pathlib.Path,
    commercial_catalog_dir: pathlib.Path | None = None,
) -> None:
    global _catalog_service
    _catalog_service = ActionCatalogService(catalog_dir, commercial_catalog_dir)
```

- [ ] **Step 4: Run catalog tests to verify they pass**

```bash
cd backend
pytest tests/unit/test_catalog_service_commercial.py -v
```

Expected: All catalog tests PASS (executor tests will still fail — handled in Task 3)

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/catalog_service.py backend/tests/unit/test_catalog_service_commercial.py
git commit -m "feat: extend catalog_service to load commercial catalog from optional second directory"
```

---

## Task 3: Add commercial executor loading to catalog_service.py

**Files:**
- Modify: `backend/app/connectors/catalog_service.py`

Commercial executors are Python files at `{commercial_catalog_dir}/../executors/{module}.py`. Since they live outside the `app` package, they're loaded with `importlib.util.spec_from_file_location` instead of `importlib.import_module`. The executor reference in a commercial catalog JSON uses the prefix `commercial.` (e.g., `"executor": "commercial.provision_instance"`).

- [ ] **Step 1: Write the failing test** (append to test file)

```python
# Add to backend/tests/unit/test_catalog_service_commercial.py

def test_commercial_executor_loaded_from_filesystem(tmp_path):
    core_dir = tmp_path / "catalog"
    core_dir.mkdir()
    comm_catalog_dir = tmp_path / "commercial" / "catalog"
    comm_catalog_dir.mkdir(parents=True)
    comm_exec_dir = tmp_path / "commercial" / "executors"
    comm_exec_dir.mkdir()

    _make_catalog(str(core_dir), "aws", "list_instances", "aws.list_instances")
    _make_catalog(str(comm_catalog_dir), "ops", "provision_instance", "commercial.provision_instance")

    # Write a minimal executor module
    (comm_exec_dir / "provision_instance.py").write_text(
        "async def execute(parameters, asset_ids, connector):\n"
        "    return {'provisioned': True}\n"
    )

    svc = ActionCatalogService(core_dir, commercial_catalog_dir=comm_catalog_dir)
    mod = svc.get_executor("ops", "provision_instance")
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(mod.execute({}, [], None))
    assert result == {"provisioned": True}
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend
pytest tests/unit/test_catalog_service_commercial.py::test_commercial_executor_loaded_from_filesystem -v
```

Expected: FAIL — `ImportError` or `ValueError` on executor ref `commercial.provision_instance`

- [ ] **Step 3: Extend get_executor to handle commercial prefix**

Replace the `get_executor` method in `backend/app/connectors/catalog_service.py`:

```python
    def get_executor(self, connector_type: str, action_id: str) -> types.ModuleType:
        """Resolves executor reference to an importable module.
        Caller accesses execute() and rollback() as module attributes.

        Core executors: "aws.list_instances" → app.connectors.executors.aws.list_instances
        Commercial executors: "commercial.provision_instance" → loaded from
            {commercial_catalog_dir}/../executors/provision_instance.py
        """
        action_def = self.get_action_def(connector_type, action_id)
        executor_ref = action_def.get("executor", "")
        parts = executor_ref.split(".")
        if len(parts) != 2:
            raise ValueError(f"Invalid executor reference '{executor_ref}' — expected 'connector.module'")

        if parts[0] == "commercial":
            return self._load_commercial_executor(parts[1])

        module_path = f"app.connectors.executors.{parts[0]}.{parts[1]}"
        try:
            return importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            raise ImportError(f"Executor module '{module_path}' not found: {exc}") from exc

    def _load_commercial_executor(self, module_name: str) -> types.ModuleType:
        """Load a commercial executor from {commercial_dir}/../executors/{module_name}.py."""
        import importlib.util
        if self._commercial_dir is None:
            raise ImportError(
                f"Commercial executor '{module_name}' requested but no commercial catalog dir is set"
            )
        exec_path = self._commercial_dir.parent / "executors" / f"{module_name}.py"
        if not exec_path.exists():
            raise ImportError(
                f"Commercial executor not found at '{exec_path}'"
            )
        spec = importlib.util.spec_from_file_location(f"commercial.{module_name}", exec_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module
```

- [ ] **Step 4: Run all catalog tests**

```bash
cd backend
pytest tests/unit/test_catalog_service_commercial.py -v
```

Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/catalog_service.py
git commit -m "feat: add commercial executor loading via filesystem path for commercial. prefix"
```

---

## Task 4: Wire commercial catalog path into main.py

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write the test**

```python
# Add to backend/tests/unit/test_catalog_service_commercial.py

def test_init_catalog_service_accepts_commercial_dir():
    """init_catalog_service passes commercial_catalog_dir through to ActionCatalogService."""
    from app.connectors.catalog_service import init_catalog_service, get_catalog_service
    import app.connectors.catalog_service as cs_mod

    with tempfile.TemporaryDirectory() as core_dir, \
         tempfile.TemporaryDirectory() as comm_dir:
        _make_catalog(core_dir, "aws", "list_instances", "aws.list_instances")
        _make_catalog(comm_dir, "ops", "provision_instance", "commercial.provision_instance")

        # Reset global state
        cs_mod._catalog_service = None
        init_catalog_service(
            pathlib.Path(core_dir),
            commercial_catalog_dir=pathlib.Path(comm_dir),
        )
        svc = get_catalog_service()
        assert "ops" in svc.list_connector_types()
        # Cleanup
        cs_mod._catalog_service = None
```

- [ ] **Step 2: Run to verify it passes** (already passes from Task 2)

```bash
cd backend
pytest tests/unit/test_catalog_service_commercial.py::test_init_catalog_service_accepts_commercial_dir -v
```

Expected: PASS (the signature was already updated in Task 2)

- [ ] **Step 3: Update main.py to pass commercial path from settings**

Find the `init_catalog_service` call in `backend/app/main.py` (currently around line 54):

```python
    init_catalog_service(pathlib.Path(__file__).parent / "connectors" / "catalog")
```

Replace it with:

```python
    from app.config import settings
    _commercial_catalog_path = (
        pathlib.Path(settings.NEXPLANE_COMMERCIAL_CATALOG_PATH)
        if settings.NEXPLANE_COMMERCIAL_CATALOG_PATH
        else None
    )
    init_catalog_service(
        pathlib.Path(__file__).parent / "connectors" / "catalog",
        commercial_catalog_dir=_commercial_catalog_path,
    )
```

- [ ] **Step 4: Start the backend and verify it starts cleanly without the env var set**

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# Look for: "INFO: Application startup complete." with no errors
# Ctrl+C to stop
```

Expected: clean startup, no errors about commercial catalog

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: wire NEXPLANE_COMMERCIAL_CATALOG_PATH from settings into catalog service init"
```

---

## Task 5: Create nexplane-deploy repo skeleton

This task creates the new `nexplane-deploy` repo. All work happens outside the `nexplane` directory.

- [ ] **Step 1: Create the repo on GitHub**

```bash
gh repo create youbetyourballs/nexplane-deploy --private --description "Nexplane commercial deployment harness and ops overlay"
```

Expected: repo URL printed, e.g. `https://github.com/youbetyourballs/nexplane-deploy`

- [ ] **Step 2: Clone it locally**

```bash
# From your preferred parent directory (e.g. C:\Users\john\ or /f/Nexplane/)
git clone git@github.com:youbetyourballs/nexplane-deploy.git
cd nexplane-deploy
```

- [ ] **Step 3: Create directory structure**

```bash
mkdir -p bootstrap/scripts catalog executors client-registry smoke artifacts demo-seeds
touch catalog/.gitkeep executors/.gitkeep smoke/.gitkeep artifacts/.gitkeep demo-seeds/.gitkeep
```

- [ ] **Step 4: Write .gitignore**

```
# Terraform
**/.terraform/
*.tfstate
*.tfstate.backup
*.tfstate.lock.info
.terraform.lock.hcl
*.tfvars
!*.tfvars.example

# Environment files
.env
.env.*
!.env.*.template
!.env.example

# Python
__pycache__/
*.pyc
*.pyo

# Client data
client-registry/*.yaml
!client-registry/_template.yaml
```

Save to `.gitignore`.

- [ ] **Step 5: Write README.md**

```markdown
# nexplane-deploy

Private commercial overlay for Nexplane. Contains:

- `bootstrap/` — Terraform to provision the ops Nexplane instance and CI test ops instance
- `catalog/` — Commercial CR catalog JSON files (mounted into ops instance at runtime)
- `executors/` — Commercial CR Python executor modules
- `client-registry/` — Per-client configuration (one YAML file per client, git-tracked)
- `smoke/` — Live smoke tests for commercial CRs
- `artifacts/` — CI pipelines for Docker Compose bundle, VM image, and Helm chart packaging
- `demo-seeds/` — SQL/fixture files for demo mode trial seeding

## Ops instance setup

The ops instance runs standard `nexplane` with two extra env vars:

```
NEXPLANE_EDITION=commercial
NEXPLANE_COMMERCIAL_CATALOG_PATH=/mnt/commercial/catalog
```

The `nexplane-deploy` repo is cloned to `/home/ec2-user/nexplane-deploy` on the ops EC2.
The `bootstrap/docker-compose.ops.yml` overlay mounts it into the backend container.

## Provisioning

```bash
cd bootstrap
cp .env.ops.template .env.ops
# Fill in .env.ops
terraform init
terraform apply
```
```

- [ ] **Step 6: Commit skeleton**

```bash
git add .
git commit -m "chore: initial repo skeleton"
git push -u origin main
```

---

## Task 6: Create client registry template

**Files:**
- Create: `client-registry/_template.yaml`
- Create: `client-registry/README.md`

- [ ] **Step 1: Write _template.yaml**

```yaml
# Client registry entry — copy to <client-slug>.yaml and fill in.
# This file is git-tracked. Never put secrets here.

# Unique slug used in resource names (lowercase, hyphens only)
slug: acme-corp

# Human-readable name
name: Acme Corp

# Delivery model: managed | self-hosted-compose | self-hosted-vm | self-hosted-helm
delivery_model: managed

# Contact info (used in onboarding package generation)
admin_email: admin@acme.example
billing_contact: billing@acme.example

# Managed-only fields (ignored for self-hosted)
managed:
  aws_region: us-east-1
  instance_type: t3.medium
  # Populated by provision_instance CR output — do not edit manually
  instance_id: ""
  private_ip: ""
  tailscale_ip: ""
  instance_url: ""

# Self-hosted fields (ignored for managed)
self_hosted:
  # artifact version to send (defaults to "latest")
  artifact_version: latest
  # customer's network info for the onboarding package
  target_platform: ""  # e.g. "vmware-esxi-7", "docker-compose", "eks-1.29"

# Trial configuration
trial:
  mode: fresh  # demo | fresh
  expires_at: ""  # ISO 8601, populated by provision_instance CR

# Internal notes
notes: ""
```

Save to `client-registry/_template.yaml`.

- [ ] **Step 2: Write client-registry/README.md**

```markdown
# Client Registry

One YAML file per client, named `<slug>.yaml`. Copied from `_template.yaml`.

Files are git-tracked for auditability. **Never put secrets, passwords, or API keys here.**
Credentials are stored in the ops Nexplane instance's connector vault.

## Adding a new client

1. Copy `_template.yaml` to `<slug>.yaml`
2. Fill in the fields
3. Commit the file
4. Run the `provision_instance` CR on the ops Nexplane instance

## Fields

- `slug` — used in EC2 resource names, tags, and SSM parameter paths
- `delivery_model` — determines which provision_instance executor path runs
- `managed.instance_id` — populated automatically by the provision_instance CR; commit the update after provisioning
```

- [ ] **Step 3: Commit**

```bash
git add client-registry/
git commit -m "feat: add client registry template and README"
git push
```

---

## Task 7: Create ops Docker Compose overlay

**Files:**
- Create: `bootstrap/docker-compose.ops.yml`
- Create: `bootstrap/.env.ops.template`

- [ ] **Step 1: Write docker-compose.ops.yml**

This file is used alongside `docker-compose.prod.yml` on the ops instance:
`docker compose -f docker-compose.prod.yml -f docker-compose.ops.yml up -d`

```yaml
# Ops instance overlay — extends docker-compose.prod.yml with commercial catalog mount.
# Run alongside docker-compose.prod.yml:
#   docker compose -f /home/ec2-user/nexplane/docker-compose.prod.yml \
#                  -f /home/ec2-user/nexplane-deploy/bootstrap/docker-compose.ops.yml up -d

services:
  backend:
    environment:
      NEXPLANE_EDITION: commercial
      NEXPLANE_COMMERCIAL_CATALOG_PATH: /mnt/commercial/catalog
    volumes:
      - /home/ec2-user/nexplane-deploy:/mnt/commercial:ro
      - tailscale_state:/var/lib/tailscale-state
```

Save to `bootstrap/docker-compose.ops.yml`.

- [ ] **Step 2: Write .env.ops.template**

```bash
# Ops instance environment — copy to .env.ops and fill in.
# Used alongside .env (the standard nexplane env) when running the ops instance.
# Run: cp bootstrap/.env.ops.template .env && fill in all values

# ── Database ────────────────────────────────────────────────────────────────
POSTGRES_USER=nexplane
POSTGRES_PASSWORD=CHANGE_ME_STRONG_PASSWORD
POSTGRES_DB=nexplane

# ── Application ─────────────────────────────────────────────────────────────
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=CHANGE_ME_GENERATE_32_CHAR_HEX

ENVIRONMENT=production
CORS_ORIGINS=https://ops.nexplane.internal

# Public URL of the ops instance API (used by frontend)
NEXPLANE_API_URL=https://ops.nexplane.internal

# ── Agent Downloads ──────────────────────────────────────────────────────────
NEXPLANE_AGENT_DOWNLOAD_URL=https://nexplane-dist.s3.us-east-1.amazonaws.com

# ── Email ────────────────────────────────────────────────────────────────────
SMTP_HOST=smtp.yourprovider.com
SMTP_PORT=587
SMTP_USER=ops@nexplane.ai
SMTP_PASSWORD=CHANGE_ME
SMTP_FROM=Nexplane Ops <ops@nexplane.ai>

# ── Commercial ───────────────────────────────────────────────────────────────
# Set automatically by docker-compose.ops.yml — do not override here
# NEXPLANE_EDITION=commercial
# NEXPLANE_COMMERCIAL_CATALOG_PATH=/mnt/commercial/catalog
```

Save to `bootstrap/.env.ops.template`.

- [ ] **Step 3: Commit**

```bash
git add bootstrap/
git commit -m "feat: add ops docker-compose overlay and env template"
git push
```

---

## Task 8: Write Terraform for ops EC2 instance

**Files:**
- Create: `bootstrap/variables.tf`
- Create: `bootstrap/main.tf`
- Create: `bootstrap/outputs.tf`
- Create: `bootstrap/scripts/install.sh`

This Terraform provisions one EC2 instance (ops) or two (ops + CI test ops) depending on `var.provision_ci_instance`. It follows the same pattern as the existing nexplane platform instance (Amazon Linux 2023, SSM access, no direct SSH).

- [ ] **Step 1: Write variables.tf**

```hcl
# bootstrap/variables.tf

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type for ops instances"
  type        = string
  default     = "t3.medium"
}

variable "ops_instance_name" {
  description = "Name tag for the ops Nexplane instance"
  type        = string
  default     = "nexplane-ops"
}

variable "provision_ci_instance" {
  description = "Whether to also provision a CI test ops instance"
  type        = bool
  default     = false
}

variable "nexplane_repo_url" {
  description = "HTTPS URL of the nexplane repo (used by install.sh)"
  type        = string
  default     = "https://github.com/youbetyourballs/nexplane.git"
}

variable "nexplane_deploy_repo_url" {
  description = "HTTPS URL of the nexplane-deploy repo (used by install.sh)"
  type        = string
  default     = "https://github.com/youbetyourballs/nexplane-deploy.git"
}

variable "nexplane_git_token" {
  description = "GitHub PAT with read access to both repos (stored in SSM after provisioning)"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 2: Write main.tf**

```hcl
# bootstrap/main.tf

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ── AMI: latest Amazon Linux 2023 ─────────────────────────────────────────
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

# ── IAM: SSM + S3 read for nexplane-dist ──────────────────────────────────
resource "aws_iam_role" "ops" {
  name = "${var.ops_instance_name}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ops.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "s3_read" {
  name = "${var.ops_instance_name}-s3-read"
  role = aws_iam_role.ops.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:ListBucket"]
      Resource = [
        "arn:aws:s3:::nexplane-dist",
        "arn:aws:s3:::nexplane-dist/*",
      ]
    }]
  })
}

resource "aws_iam_role_policy" "ec2_manage" {
  name = "${var.ops_instance_name}-ec2-manage"
  role = aws_iam_role.ops.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = [
        "ec2:RunInstances", "ec2:TerminateInstances", "ec2:DescribeInstances",
        "ec2:DescribeInstanceStatus", "ec2:CreateTags",
        "ssm:SendCommand", "ssm:GetCommandInvocation",
        "ssm:DescribeInstanceInformation",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_instance_profile" "ops" {
  name = "${var.ops_instance_name}-profile"
  role = aws_iam_role.ops.name
}

# ── Security group ────────────────────────────────────────────────────────
resource "aws_security_group" "ops" {
  name        = "${var.ops_instance_name}-sg"
  description = "Nexplane ops instance"

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP (redirect to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── SSM parameter: GitHub PAT ─────────────────────────────────────────────
resource "aws_ssm_parameter" "git_token" {
  name  = "/nexplane/ops/git-token"
  type  = "SecureString"
  value = var.nexplane_git_token
}

# ── EC2: ops instance ─────────────────────────────────────────────────────
resource "aws_instance" "ops" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  iam_instance_profile   = aws_iam_instance_profile.ops.name
  vpc_security_group_ids = [aws_security_group.ops.id]

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  user_data = base64encode(templatefile("${path.module}/scripts/install.sh", {
    nexplane_repo_url        = var.nexplane_repo_url
    nexplane_deploy_repo_url = var.nexplane_deploy_repo_url
    git_token_ssm_path       = aws_ssm_parameter.git_token.name
    aws_region               = var.aws_region
  }))

  tags = {
    Name        = var.ops_instance_name
    ManagedBy   = "nexplane-deploy-terraform"
    Role        = "ops"
  }
}

# ── EC2: CI test ops instance (optional) ─────────────────────────────────
resource "aws_instance" "ops_ci" {
  count                  = var.provision_ci_instance ? 1 : 0
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  iam_instance_profile   = aws_iam_instance_profile.ops.name
  vpc_security_group_ids = [aws_security_group.ops.id]

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  user_data = base64encode(templatefile("${path.module}/scripts/install.sh", {
    nexplane_repo_url        = var.nexplane_repo_url
    nexplane_deploy_repo_url = var.nexplane_deploy_repo_url
    git_token_ssm_path       = aws_ssm_parameter.git_token.name
    aws_region               = var.aws_region
  }))

  tags = {
    Name        = "${var.ops_instance_name}-ci"
    ManagedBy   = "nexplane-deploy-terraform"
    Role        = "ops-ci"
  }
}
```

- [ ] **Step 3: Write outputs.tf**

```hcl
# bootstrap/outputs.tf

output "ops_instance_id" {
  description = "Instance ID of the ops Nexplane instance"
  value       = aws_instance.ops.id
}

output "ops_private_ip" {
  description = "Private IP of the ops instance (connect via SSM or Tailscale)"
  value       = aws_instance.ops.private_ip
}

output "ops_ci_instance_id" {
  description = "Instance ID of the CI test ops instance (empty if not provisioned)"
  value       = var.provision_ci_instance ? aws_instance.ops_ci[0].id : ""
}

output "git_token_ssm_path" {
  description = "SSM parameter path storing the GitHub PAT"
  value       = aws_ssm_parameter.git_token.name
}
```

- [ ] **Step 4: Commit Terraform files**

```bash
git add bootstrap/variables.tf bootstrap/main.tf bootstrap/outputs.tf
git commit -m "feat: terraform for ops EC2 instance with SSM, IAM, and security group"
```

---

## Task 9: Write install.sh bootstrap script

**Files:**
- Create: `bootstrap/scripts/install.sh`

This script runs as EC2 user data on first boot. It installs Docker, clones both repos, and starts the ops Nexplane stack.

- [ ] **Step 1: Write install.sh**

```bash
#!/bin/bash
# bootstrap/scripts/install.sh
# EC2 user-data: runs once on first boot to set up the nexplane ops instance.
# Terraform templatefile variables: nexplane_repo_url, nexplane_deploy_repo_url,
#   git_token_ssm_path, aws_region

set -euo pipefail
exec > >(tee /var/log/nexplane-install.log | logger -t nexplane-install) 2>&1

NEXPLANE_REPO_URL="${nexplane_repo_url}"
NEXPLANE_DEPLOY_REPO_URL="${nexplane_deploy_repo_url}"
GIT_TOKEN_SSM_PATH="${git_token_ssm_path}"
AWS_REGION="${aws_region}"
HOME_DIR="/home/ec2-user"

echo "=== Nexplane ops install starting ==="

# ── System packages ──────────────────────────────────────────────────────
dnf update -y
dnf install -y docker git jq

# ── Docker ───────────────────────────────────────────────────────────────
systemctl enable --now docker
usermod -aG docker ec2-user

# Install Docker Compose plugin
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# ── Fetch GitHub PAT from SSM ────────────────────────────────────────────
GIT_TOKEN=$(aws ssm get-parameter \
  --region "$AWS_REGION" \
  --name "$GIT_TOKEN_SSM_PATH" \
  --with-decryption \
  --query Parameter.Value \
  --output text)

# ── Clone repos ──────────────────────────────────────────────────────────
# Inject token into clone URLs
NEXPLANE_AUTH_URL="${NEXPLANE_REPO_URL/github.com/oauth2:${GIT_TOKEN}@github.com}"
DEPLOY_AUTH_URL="${NEXPLANE_DEPLOY_REPO_URL/github.com/oauth2:${GIT_TOKEN}@github.com}"

git clone "$NEXPLANE_AUTH_URL" "$HOME_DIR/nexplane"
git clone "$DEPLOY_AUTH_URL" "$HOME_DIR/nexplane-deploy"
chown -R ec2-user:ec2-user "$HOME_DIR/nexplane" "$HOME_DIR/nexplane-deploy"

# ── Copy env template ─────────────────────────────────────────────────────
cp "$HOME_DIR/nexplane-deploy/bootstrap/.env.ops.template" "$HOME_DIR/nexplane/.env"
echo "NOTE: Edit $HOME_DIR/nexplane/.env before starting the stack."

# ── Write systemd service for auto-restart ────────────────────────────────
cat > /etc/systemd/system/nexplane-ops.service << 'EOF'
[Unit]
Description=Nexplane Ops Instance
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/ec2-user/nexplane
ExecStart=/usr/bin/docker compose \
  -f docker-compose.prod.yml \
  -f /home/ec2-user/nexplane-deploy/bootstrap/docker-compose.ops.yml \
  up -d
ExecStop=/usr/bin/docker compose \
  -f docker-compose.prod.yml \
  -f /home/ec2-user/nexplane-deploy/bootstrap/docker-compose.ops.yml \
  down
User=ec2-user
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nexplane-ops

echo "=== Install complete. Fill in /home/ec2-user/nexplane/.env then: ==="
echo "    sudo systemctl start nexplane-ops"
```

Save to `bootstrap/scripts/install.sh`.

- [ ] **Step 2: Make it executable**

```bash
chmod +x bootstrap/scripts/install.sh
```

- [ ] **Step 3: Commit**

```bash
git add bootstrap/scripts/install.sh
git commit -m "feat: EC2 user-data install script — Docker, clone repos, systemd service"
git push
```

---

## Task 10: Verify terraform init and plan (no apply yet)

- [ ] **Step 1: Create a tfvars example**

```hcl
# bootstrap/terraform.tfvars.example
# Copy to terraform.tfvars (gitignored) and fill in

aws_region               = "us-east-1"
instance_type            = "t3.medium"
ops_instance_name        = "nexplane-ops"
provision_ci_instance    = false
nexplane_repo_url        = "https://github.com/youbetyourballs/nexplane.git"
nexplane_deploy_repo_url = "https://github.com/youbetyourballs/nexplane-deploy.git"
nexplane_git_token       = "ghp_REPLACE_ME"
```

Save to `bootstrap/terraform.tfvars.example`.

- [ ] **Step 2: Run terraform init from the nexplane backend container (which has TF pre-installed)**

```bash
# On EC2, inside the backend container, or locally if terraform is installed:
cd bootstrap
terraform init
```

Expected: `Terraform has been successfully initialized!`

- [ ] **Step 3: Run terraform plan with example values**

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars to set a dummy token (plan doesn't apply)
terraform plan
```

Expected: plan output showing EC2 instance, IAM role, security group, SSM parameter — no errors

- [ ] **Step 4: Commit tfvars example**

```bash
git add bootstrap/terraform.tfvars.example
git commit -m "chore: add terraform.tfvars.example"
git push
```

---

## Task 11: Push nexplane changes and run existing test suite

- [ ] **Step 1: Run the full nexplane backend test suite to confirm no regressions**

```bash
cd /f/Nexplane/nexplane/backend
pytest tests/unit/ -v --tb=short 2>&1 | tail -30
```

Expected: all existing tests pass, plus the 8 new `test_catalog_service_commercial` tests

- [ ] **Step 2: Push nexplane changes**

```bash
cd /f/Nexplane/nexplane
git push
```

- [ ] **Step 3: SCP updated files to EC2 and restart backend**

```bash
scp -i ~/.ssh/id_ed25519 \
  backend/app/config.py \
  backend/app/connectors/catalog_service.py \
  backend/app/main.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/

# catalog_service.py lives in connectors subdir
scp -i ~/.ssh/id_ed25519 \
  backend/app/connectors/catalog_service.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/connectors/

aws ssm send-command \
  --instance-id i-050bab85006f0b73c \
  --document-name AWS-RunShellScript \
  --parameters '{"commands":["docker restart nexplane-backend-1"]}' \
  --query 'Command.CommandId' --output text
```

Wait 15 seconds, then verify:

```bash
aws ssm send-command \
  --instance-id i-050bab85006f0b73c \
  --document-name AWS-RunShellScript \
  --parameters '{"commands":["docker logs nexplane-backend-1 --tail 5 2>&1"]}' \
  --query 'Command.CommandId' --output text
# (wait 8s, then get-command-invocation)
```

Expected: `INFO: Uvicorn running on http://0.0.0.0:8000` with no errors about commercial catalog

- [ ] **Step 4: Final commit check**

```bash
cd /f/Nexplane/nexplane
git log --oneline -5
```

Expected: 4 commits from this sub-project (config, catalog_service x2, main.py)

---

## Self-Review Checklist

- [x] Spec coverage: repo skeleton ✅, commercial catalog loading ✅, ops docker-compose overlay ✅, Terraform EC2 ✅, install.sh ✅, client registry template ✅
- [x] No placeholders — all code blocks are complete
- [x] Type consistency — `commercial_catalog_dir: pathlib.Path | None` used throughout Tasks 2–4
- [x] `_commercial_dir` set in `__init__` before `_load_commercial_executor` can reference it
- [x] Terraform `templatefile()` variables match `install.sh` template variables exactly
- [x] `.gitignore` excludes `client-registry/*.yaml` except `_template.yaml` ✅
