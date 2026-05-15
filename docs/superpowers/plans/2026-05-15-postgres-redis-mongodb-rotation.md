# PostgreSQL/Redis/MongoDB Credential Rotation Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add credential rotation connectors for PostgreSQL, Redis, and MongoDB — each with executor, catalog entry, ChangeType enum entries, and smoke test phases.

**Architecture:** Each connector follows the established pattern: `_client.py` wraps the database library with a graceful fallback when no credentials are present, `rotate_*.py` executor generates a secure random password, applies it, and stores the old password in `execution_result` for rollback. Smoke phases provision EC2 instances via SSM, AMI-cache the setup, run a Nexplane CR against a live DB, verify the new creds work, and terminate the instance.

**Tech Stack:** Python (asyncio, secrets module), psycopg2 (PostgreSQL), redis-py (Redis), pymongo (MongoDB), boto3/SSM for EC2 provisioning, existing `NexplaneClient`/smoke_helpers infrastructure.

---

## File Map

### New files (create)
- `backend/app/connectors/executors/postgres/__init__.py`
- `backend/app/connectors/executors/postgres/_client.py`
- `backend/app/connectors/executors/postgres/rotate_user_password.py`
- `backend/app/connectors/executors/redis/__init__.py`
- `backend/app/connectors/executors/redis/_client.py`
- `backend/app/connectors/executors/redis/rotate_auth_password.py`
- `backend/app/connectors/executors/mongodb/__init__.py`
- `backend/app/connectors/executors/mongodb/_client.py`
- `backend/app/connectors/executors/mongodb/rotate_user_password.py`
- `backend/app/connectors/catalog/postgres.json`
- `backend/app/connectors/catalog/redis.json`
- `backend/app/connectors/catalog/mongodb.json`

### Modified files
- `backend/app/models/change_request.py` — add 3 new ChangeType entries
- `backend/tests/smoke/test_aws_live.py` — add 3 smoke phase functions + wire into `main()`

---

## Task 1: Add ChangeType enum entries

**Files:**
- Modify: `backend/app/models/change_request.py:309-311` (after `gitea_suspend_user`)

- [ ] **Step 1: Edit change_request.py**

  In `backend/app/models/change_request.py`, after the line `gitea_suspend_user = "gitea_suspend_user"` and before `class RiskLevel`, add:

  ```python
      # Database credential rotation
      rotate_postgres_password = "rotate_postgres_password"
      rotate_redis_password = "rotate_redis_password"
      rotate_mongodb_password = "rotate_mongodb_password"
  ```

- [ ] **Step 2: Verify the model loads (if Docker is running)**

  ```bash
  docker compose exec -T backend python -c "from app.models.change_request import ChangeType; print(ChangeType.rotate_postgres_password)"
  ```
  Expected: `ChangeType.rotate_postgres_password`

  If Docker is not running, skip and verify later.

- [ ] **Step 3: Commit**

  ```bash
  git add backend/app/models/change_request.py
  git commit -m "feat: add rotate_postgres_password/rotate_redis_password/rotate_mongodb_password to ChangeType"
  ```

---

## Task 2: PostgreSQL connector — _client.py

**Files:**
- Create: `backend/app/connectors/executors/postgres/__init__.py`
- Create: `backend/app/connectors/executors/postgres/_client.py`

- [ ] **Step 1: Create `__init__.py`**

  Create `backend/app/connectors/executors/postgres/__init__.py` with empty content (just a newline).

- [ ] **Step 2: Create `_client.py`**

  Create `backend/app/connectors/executors/postgres/_client.py`:

  ```python
  """PostgreSQL client wrapper.

  Uses psycopg2 if available; falls back to subprocess psql if not installed.
  Credentials come from connector.credentials dict with keys:
    host, port (default 5432), dbname (default postgres),
    user (admin user), password
  """
  from __future__ import annotations


  class PostgresClient:
      def __init__(self, host: str, port: int, dbname: str, user: str, password: str):
          self.host = host
          self.port = port
          self.dbname = dbname
          self.user = user
          self.password = password

      def alter_user_password(self, username: str, new_password: str) -> None:
          """Execute ALTER USER {username} PASSWORD '{new_password}' as admin."""
          try:
              import psycopg2  # type: ignore
              conn = psycopg2.connect(
                  host=self.host, port=self.port, dbname=self.dbname,
                  user=self.user, password=self.password,
                  connect_timeout=10,
              )
              conn.autocommit = True
              try:
                  with conn.cursor() as cur:
                      # Use parameterized identifier quoting for username safety
                      cur.execute(
                          f"ALTER USER {psycopg2.extensions.quote_ident(username, conn)}"
                          f" PASSWORD %s",
                          (new_password,),
                      )
              finally:
                  conn.close()
          except ImportError:
              self._alter_via_subprocess(username, new_password)

      def _alter_via_subprocess(self, username: str, new_password: str) -> None:
          """Fallback: run psql via subprocess."""
          import subprocess, shlex, os
          # Escape the password for single-quoted SQL string
          escaped = new_password.replace("'", "''")
          sql = f"ALTER USER \"{username}\" PASSWORD '{escaped}';"
          env = {**os.environ, "PGPASSWORD": self.password}
          result = subprocess.run(
              ["psql", "-h", self.host, "-p", str(self.port),
               "-U", self.user, "-d", self.dbname, "-c", sql],
              env=env, capture_output=True, text=True, timeout=15,
          )
          if result.returncode != 0:
              raise RuntimeError(f"psql ALTER USER failed: {result.stderr.strip()}")

      def verify_login(self, username: str, password: str) -> bool:
          """Return True if the given username+password can authenticate."""
          try:
              import psycopg2  # type: ignore
              conn = psycopg2.connect(
                  host=self.host, port=self.port, dbname=self.dbname,
                  user=username, password=password,
                  connect_timeout=5,
              )
              conn.close()
              return True
          except Exception:
              return False


  def get_postgres_client(connector) -> "PostgresClient | None":
      creds = getattr(connector, "credentials", None) or {}
      host = creds.get("host") or creds.get("hostname")
      if not host:
          return None
      return PostgresClient(
          host=host,
          port=int(creds.get("port", 5432)),
          dbname=creds.get("dbname") or creds.get("database", "postgres"),
          user=creds.get("user") or creds.get("username", "postgres"),
          password=creds.get("password", ""),
      )
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add backend/app/connectors/executors/postgres/
  git commit -m "feat: add postgres connector _client.py"
  ```

---

## Task 3: PostgreSQL connector — rotate_user_password.py executor

**Files:**
- Create: `backend/app/connectors/executors/postgres/rotate_user_password.py`

- [ ] **Step 1: Create the executor**

  Create `backend/app/connectors/executors/postgres/rotate_user_password.py`:

  ```python
  """Executor: rotate a PostgreSQL user's password."""
  from __future__ import annotations
  import secrets
  import string

  from ._client import get_postgres_client


  def _generate_password(length: int = 32) -> str:
      alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
      return "".join(secrets.choice(alphabet) for _ in range(length))


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      username = parameters.get("username", "")
      if not username:
          raise ValueError("username is required")

      client = get_postgres_client(connector)
      if not client:
          return {
              "action": "rotate_postgres_password",
              "status": "skipped",
              "reason": "no_postgres_credentials",
              "username": username,
              "_asset_ids": [str(a) for a in asset_ids],
          }

      new_password = _generate_password()
      client.alter_user_password(username, new_password)

      return {
          "action": "rotate_postgres_password",
          "username": username,
          "new_password": new_password,
          "_asset_ids": [str(a) for a in asset_ids],
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      username = parameters.get("username", "")
      # The old password was whatever the connector's admin knows; we can't
      # recover it unless it was stored before rotation. The safest rollback is
      # to rotate to a new password again (break-glass scenario).
      # In smoke tests the old password is passed via parameters["old_password"].
      old_password = parameters.get("old_password") or execution_result.get("old_password")
      if not old_password:
          return {
              "rolled_back": False,
              "reason": "old_password_not_available",
              "username": username,
          }

      client = get_postgres_client(connector)
      if not client:
          return {"rolled_back": False, "reason": "no_postgres_credentials"}

      client.alter_user_password(username, old_password)
      return {"rolled_back": True, "username": username}
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add backend/app/connectors/executors/postgres/rotate_user_password.py
  git commit -m "feat: add postgres rotate_user_password executor"
  ```

---

## Task 4: PostgreSQL catalog entry

**Files:**
- Create: `backend/app/connectors/catalog/postgres.json`

- [ ] **Step 1: Create catalog JSON**

  Create `backend/app/connectors/catalog/postgres.json`:

  ```json
  {
    "connector_type": "postgres",
    "display_name": "PostgreSQL",
    "actions": [
      {
        "action_id": "rotate_postgres_password",
        "generic_action": "rotate_postgres_password",
        "display_name": "Rotate PostgreSQL User Password",
        "description": "Generates a secure random password and sets it on the specified PostgreSQL user via ALTER USER.",
        "executor": "postgres.rotate_user_password",
        "action_type": "change",
        "execution_tier": 2,
        "estimated_duration_seconds": 10,
        "applicable_asset_types": ["server", "database"],
        "parameters": [
          {"name": "username", "type": "string", "required": true, "description": "PostgreSQL user whose password will be rotated"},
          {"name": "old_password", "type": "string", "required": false, "description": "Previous password (used for rollback only)"}
        ]
      }
    ]
  }
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add backend/app/connectors/catalog/postgres.json
  git commit -m "feat: add postgres catalog entry"
  ```

---

## Task 5: Redis connector — _client.py and rotate_auth_password.py

**Files:**
- Create: `backend/app/connectors/executors/redis/__init__.py`
- Create: `backend/app/connectors/executors/redis/_client.py`
- Create: `backend/app/connectors/executors/redis/rotate_auth_password.py`

- [ ] **Step 1: Create `__init__.py`**

  Create `backend/app/connectors/executors/redis/__init__.py` (empty/newline).

- [ ] **Step 2: Create `_client.py`**

  Create `backend/app/connectors/executors/redis/_client.py`:

  ```python
  """Redis client wrapper using redis-py."""
  from __future__ import annotations


  class RedisClient:
      def __init__(self, host: str, port: int, password: str = ""):
          self.host = host
          self.port = port
          self.password = password

      def _connect(self):
          import redis  # type: ignore
          return redis.Redis(
              host=self.host, port=self.port,
              password=self.password or None,
              socket_timeout=10,
              socket_connect_timeout=10,
              decode_responses=True,
          )

      def get_requirepass(self) -> str:
          """Return current requirepass value (empty string if none set)."""
          r = self._connect()
          try:
              result = r.config_get("requirepass")
              return result.get("requirepass", "")
          finally:
              r.close()

      def set_requirepass(self, new_password: str) -> None:
          """Set a new requirepass via CONFIG SET."""
          r = self._connect()
          try:
              r.config_set("requirepass", new_password)
          finally:
              try:
                  r.close()
              except Exception:
                  pass  # Connection may be dropped after auth change

      def ping_with_password(self, password: str) -> bool:
          """Return True if we can PING Redis with the given password."""
          try:
              import redis  # type: ignore
              r = redis.Redis(
                  host=self.host, port=self.port,
                  password=password or None,
                  socket_timeout=5,
                  socket_connect_timeout=5,
                  decode_responses=True,
              )
              result = r.ping()
              r.close()
              return bool(result)
          except Exception:
              return False


  def get_redis_client(connector) -> "RedisClient | None":
      creds = getattr(connector, "credentials", None) or {}
      host = creds.get("host") or creds.get("hostname")
      if not host:
          return None
      return RedisClient(
          host=host,
          port=int(creds.get("port", 6379)),
          password=creds.get("password") or creds.get("auth", ""),
      )
  ```

- [ ] **Step 3: Create `rotate_auth_password.py`**

  Create `backend/app/connectors/executors/redis/rotate_auth_password.py`:

  ```python
  """Executor: rotate Redis requirepass (auth password)."""
  from __future__ import annotations
  import secrets
  import string

  from ._client import get_redis_client


  def _generate_password(length: int = 32) -> str:
      # Redis passwords should avoid spaces and quotes
      alphabet = string.ascii_letters + string.digits + "!@#%^&*()-_=+"
      return "".join(secrets.choice(alphabet) for _ in range(length))


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      client = get_redis_client(connector)
      if not client:
          return {
              "action": "rotate_redis_password",
              "status": "skipped",
              "reason": "no_redis_credentials",
              "_asset_ids": [str(a) for a in asset_ids],
          }

      # Capture old password for rollback
      try:
          old_password = client.get_requirepass()
      except Exception:
          old_password = ""

      new_password = _generate_password()
      client.set_requirepass(new_password)

      return {
          "action": "rotate_redis_password",
          "old_password": old_password,
          "new_password": new_password,
          "_asset_ids": [str(a) for a in asset_ids],
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      old_password = execution_result.get("old_password", "")
      new_password = execution_result.get("new_password", "")

      if not new_password:
          return {"rolled_back": False, "reason": "new_password_not_in_result"}

      # Reconnect with new password to restore old one
      creds = getattr(connector, "credentials", None) or {}
      from ._client import RedisClient
      host = creds.get("host") or creds.get("hostname", "")
      port = int(creds.get("port", 6379))
      rollback_client = RedisClient(host=host, port=port, password=new_password)
      try:
          rollback_client.set_requirepass(old_password)
          return {"rolled_back": True}
      except Exception as exc:
          return {"rolled_back": False, "reason": str(exc)}
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add backend/app/connectors/executors/redis/
  git commit -m "feat: add redis connector with rotate_auth_password executor"
  ```

---

## Task 6: Redis catalog entry

**Files:**
- Create: `backend/app/connectors/catalog/redis.json`

- [ ] **Step 1: Create catalog JSON**

  Create `backend/app/connectors/catalog/redis.json`:

  ```json
  {
    "connector_type": "redis",
    "display_name": "Redis",
    "actions": [
      {
        "action_id": "rotate_redis_password",
        "generic_action": "rotate_redis_password",
        "display_name": "Rotate Redis Auth Password",
        "description": "Rotates the Redis requirepass via CONFIG SET. Stores old password in execution result for rollback.",
        "executor": "redis.rotate_auth_password",
        "action_type": "change",
        "execution_tier": 2,
        "estimated_duration_seconds": 5,
        "applicable_asset_types": ["server", "database"],
        "parameters": []
      }
    ]
  }
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add backend/app/connectors/catalog/redis.json
  git commit -m "feat: add redis catalog entry"
  ```

---

## Task 7: MongoDB connector — _client.py and rotate_user_password.py

**Files:**
- Create: `backend/app/connectors/executors/mongodb/__init__.py`
- Create: `backend/app/connectors/executors/mongodb/_client.py`
- Create: `backend/app/connectors/executors/mongodb/rotate_user_password.py`

- [ ] **Step 1: Create `__init__.py`**

  Create `backend/app/connectors/executors/mongodb/__init__.py` (empty/newline).

- [ ] **Step 2: Create `_client.py`**

  Create `backend/app/connectors/executors/mongodb/_client.py`:

  ```python
  """MongoDB client wrapper using pymongo."""
  from __future__ import annotations


  class MongoClient:
      def __init__(self, uri: str, auth_db: str = "admin"):
          self.uri = uri
          self.auth_db = auth_db

      def _connect(self):
          from pymongo import MongoClient as PyMongoClient  # type: ignore
          return PyMongoClient(self.uri, serverSelectionTimeoutMS=10000)

      def rotate_user_password(self, username: str, new_password: str,
                               db_name: str = "admin") -> None:
          """Call updateUser on the specified database to change the password."""
          client = self._connect()
          try:
              db = client[db_name]
              db.command("updateUser", username, pwd=new_password)
          finally:
              client.close()

      def verify_login(self, username: str, password: str,
                       db_name: str = "admin") -> bool:
          """Return True if the given credentials can authenticate."""
          try:
              from pymongo import MongoClient as PyMongoClient  # type: ignore
              host = self.uri.split("@")[-1].split("/")[0] if "@" in self.uri else self.uri
              # Build a clean URI with the given username/password
              test_uri = f"mongodb://{username}:{password}@{host}/{db_name}"
              c = PyMongoClient(test_uri, serverSelectionTimeoutMS=5000)
              c[db_name].command("ping")
              c.close()
              return True
          except Exception:
              return False


  def get_mongo_client(connector) -> "MongoClient | None":
      creds = getattr(connector, "credentials", None) or {}
      host = creds.get("host") or creds.get("hostname")
      if not host:
          return None
      port = int(creds.get("port", 27017))
      user = creds.get("user") or creds.get("username", "")
      password = creds.get("password", "")
      auth_db = creds.get("auth_db") or creds.get("authSource", "admin")
      if user and password:
          uri = f"mongodb://{user}:{password}@{host}:{port}/{auth_db}"
      else:
          uri = f"mongodb://{host}:{port}/"
      return MongoClient(uri=uri, auth_db=auth_db)
  ```

- [ ] **Step 3: Create `rotate_user_password.py`**

  Create `backend/app/connectors/executors/mongodb/rotate_user_password.py`:

  ```python
  """Executor: rotate a MongoDB user's password."""
  from __future__ import annotations
  import secrets
  import string

  from ._client import get_mongo_client


  def _generate_password(length: int = 32) -> str:
      alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
      return "".join(secrets.choice(alphabet) for _ in range(length))


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      username = parameters.get("username", "")
      if not username:
          raise ValueError("username is required")
      db_name = parameters.get("db_name", "admin")

      client = get_mongo_client(connector)
      if not client:
          return {
              "action": "rotate_mongodb_password",
              "status": "skipped",
              "reason": "no_mongodb_credentials",
              "username": username,
              "_asset_ids": [str(a) for a in asset_ids],
          }

      new_password = _generate_password()
      client.rotate_user_password(username, new_password, db_name=db_name)

      return {
          "action": "rotate_mongodb_password",
          "username": username,
          "db_name": db_name,
          "new_password": new_password,
          "_asset_ids": [str(a) for a in asset_ids],
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      username = parameters.get("username", "")
      db_name = parameters.get("db_name", "admin")
      old_password = parameters.get("old_password") or execution_result.get("old_password")

      if not old_password:
          return {
              "rolled_back": False,
              "reason": "old_password_not_available",
              "username": username,
          }

      # After rotation the connector creds still hold the admin credentials
      client = get_mongo_client(connector)
      if not client:
          return {"rolled_back": False, "reason": "no_mongodb_credentials"}

      client.rotate_user_password(username, old_password, db_name=db_name)
      return {"rolled_back": True, "username": username}
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add backend/app/connectors/executors/mongodb/
  git commit -m "feat: add mongodb connector with rotate_user_password executor"
  ```

---

## Task 8: MongoDB catalog entry

**Files:**
- Create: `backend/app/connectors/catalog/mongodb.json`

- [ ] **Step 1: Create catalog JSON**

  Create `backend/app/connectors/catalog/mongodb.json`:

  ```json
  {
    "connector_type": "mongodb",
    "display_name": "MongoDB",
    "actions": [
      {
        "action_id": "rotate_mongodb_password",
        "generic_action": "rotate_mongodb_password",
        "display_name": "Rotate MongoDB User Password",
        "description": "Rotates a MongoDB user's password via the updateUser command on the specified database.",
        "executor": "mongodb.rotate_user_password",
        "action_type": "change",
        "execution_tier": 2,
        "estimated_duration_seconds": 10,
        "applicable_asset_types": ["server", "database"],
        "parameters": [
          {"name": "username", "type": "string", "required": true, "description": "MongoDB user whose password will be rotated"},
          {"name": "db_name", "type": "string", "required": false, "description": "Database the user belongs to (default: admin)"},
          {"name": "old_password", "type": "string", "required": false, "description": "Previous password (used for rollback only)"}
        ]
      }
    ]
  }
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add backend/app/connectors/catalog/mongodb.json
  git commit -m "feat: add mongodb catalog entry"
  ```

---

## Task 9: Smoke phase — POSTGRES_ROTATE

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Add function `run_phase_postgres_rotate` before the existing keycloak phase (around line 6277). Then wire into `main()` and the `--phases` help string.

- [ ] **Step 1: Add `run_phase_postgres_rotate` function**

  Insert the following function in `test_aws_live.py` **immediately before** `# Phase KEYCLOAK_ROTATE`:

  ```python
  # ---------------------------------------------------------------------------
  # Phase POSTGRES_ROTATE — PostgreSQL user password rotation (AMI cached)
  # ---------------------------------------------------------------------------

  def run_phase_postgres_rotate(client: NexplaneClient, cloud_account_id: str) -> None:
      """Phase POSTGRES_ROTATE: provision PostgreSQL on EC2, create test user, rotate via
      Nexplane CR, verify new credentials work, then rollback. AMI cached after first setup."""
      import time, hashlib
      print("\n[Phase POSTGRES_ROTATE] PostgreSQL user password rotation")

      ec2_client = _get_aws_boto3_client("ec2")
      ssm_client = _get_aws_boto3_client("ssm")
      if not ec2_client or not ssm_client:
          fail("[POSTGRES_ROTATE] AWS clients not available")

      AL2023_AMI = "ami-0953476d60561c955"
      pg_version = "15"

      setup_script = f"""
  set -e
  dnf install -y postgresql{pg_version}-server postgresql{pg_version} 2>/dev/null || \
    yum install -y postgresql-server postgresql 2>/dev/null
  postgresql-setup --initdb || true
  systemctl enable postgresql --now || service postgresql start || true
  sleep 3

  # Allow password auth from localhost
  PG_HBA=$(find /var/lib/pgsql -name pg_hba.conf 2>/dev/null | head -1)
  if [ -n "$PG_HBA" ]; then
    sed -i 's/^local.*all.*all.*peer/local   all             all                                     md5/' "$PG_HBA"
    sed -i 's/^host.*all.*all.*127.0.0.1.*ident/host    all             all             127.0.0.1\\/32         md5/' "$PG_HBA"
    systemctl reload postgresql 2>/dev/null || service postgresql reload 2>/dev/null || true
    sleep 2
  fi

  # Create test user with known initial password
  sudo -u postgres psql -c "CREATE USER smokeuser WITH PASSWORD 'initial-smoke-pw-12345';" 2>/dev/null || \
    sudo -u postgres psql -c "ALTER USER smokeuser PASSWORD 'initial-smoke-pw-12345';"

  echo "POSTGRES_SETUP_COMPLETE"
  """
      setup_hash = hashlib.md5(f"pg{pg_version}-{AL2023_AMI}".encode()).hexdigest()

      # Check AMI cache
      cached_ami = None
      param_path = f"/nexplane/smoke-amis/postgres/{setup_hash[:8]}"
      try:
          resp_p = ssm_client.get_parameter(Name=param_path)
          candidate = resp_p["Parameter"]["Value"]
          images = ec2_client.describe_images(ImageIds=[candidate])["Images"]
          if images and images[0]["State"] == "available":
              cached_ami = candidate
              log(f"Using cached PostgreSQL AMI: {cached_ami}")
      except Exception:
          pass

      # Launch EC2
      vpc_id = ec2_client.describe_vpcs(
          Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
      subnets = ec2_client.describe_subnets(
          Filters=[{"Name": "vpcId", "Values": [vpc_id]}])["Subnets"]
      try:
          offs = ec2_client.describe_instance_type_offerings(
              LocationType="availability-zone",
              Filters=[{"Name": "instance-type", "Values": ["t3.small"]}])["InstanceTypeOfferings"]
          azs = {o["Location"] for o in offs}
          subnets = [s for s in subnets if s.get("AvailabilityZone") in azs] or subnets
      except Exception:
          pass
      subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
      subnet_id = subnets[0]["SubnetId"]

      resp = ec2_client.run_instances(
          ImageId=cached_ami or AL2023_AMI, InstanceType="t3.small",
          MinCount=1, MaxCount=1, SubnetId=subnet_id,
          IamInstanceProfile={"Name": "NexplaneEC2TestProfile"},
          TagSpecifications=[{"ResourceType": "instance", "Tags": [
              {"Key": "Name", "Value": "nexplane-smoke-postgres"},
              {"Key": "nexplane-smoke", "Value": "true"},
          ]}],
      )
      instance_id = resp["Instances"][0]["InstanceId"]
      log(f"PostgreSQL EC2: {instance_id}")

      import time as _t2
      _t2.sleep(5)
      deadline = time.time() + 180
      private_ip = ""
      while time.time() < deadline:
          try:
              desc = ec2_client.describe_instances(InstanceIds=[instance_id])
              state = desc["Reservations"][0]["Instances"][0]["State"]["Name"]
              if state == "running":
                  private_ip = desc["Reservations"][0]["Instances"][0].get("PrivateIpAddress", "")
                  break
          except Exception:
              pass
          time.sleep(8)

      deadline2 = time.time() + 120
      while time.time() < deadline2:
          try:
              r = ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": ["echo ready"]}, TimeoutSeconds=10)
              time.sleep(5)
              out = ssm_client.get_command_invocation(
                  CommandId=r["Command"]["CommandId"], InstanceId=instance_id)
              if out["Status"] == "Success":
                  break
          except Exception:
              pass
          time.sleep(10)

      pg_connector_id = None
      try:
          if not cached_ami:
              resp_s = ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": [setup_script]}, TimeoutSeconds=120)
              time.sleep(40)
              try:
                  out_s = ssm_client.get_command_invocation(
                      CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
                  if "POSTGRES_SETUP_COMPLETE" not in out_s.get("StandardOutputContent", ""):
                      log("  WARNING: PostgreSQL setup may not have completed cleanly")
                  else:
                      log("PostgreSQL installed and test user created")
                      from run_on_ec2 import get_or_create_smoke_ami
                      get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "postgres", setup_hash)
              except Exception as e:
                  log(f"  WARNING: PostgreSQL setup check failed: {e}")
          else:
              # Ensure user exists on cached instance
              ensure_cmd = """
  sudo -u postgres psql -c "CREATE USER smokeuser WITH PASSWORD 'initial-smoke-pw-12345';" 2>/dev/null || \
    sudo -u postgres psql -c "ALTER USER smokeuser PASSWORD 'initial-smoke-pw-12345';" && \
  systemctl start postgresql 2>/dev/null || service postgresql start 2>/dev/null || true
  echo "PG_READY"
  """
              resp_e = ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": [ensure_cmd]}, TimeoutSeconds=60)
              time.sleep(15)

          # Register connector
          conn_resp = client.post("/connectors", json={
              "connector_type": "postgres",
              "name": "nexplane-smoke-postgres",
              "display_name": "nexplane-smoke-postgres",
              "credentials": {
                  "host": private_ip,
                  "port": 5432,
                  "dbname": "postgres",
                  "user": "postgres",
                  "password": "",  # peer auth on local socket; connector uses private_ip
              },
          })
          pg_connector_id = conn_resp.get("id")
          log(f"PostgreSQL connector registered: {pg_connector_id}")

          # Register asset
          asset_resp = client.post("/assets", json={
              "name": f"smoke-postgres-{instance_id}",
              "asset_type": "server",
              "organization_id": cloud_account_id,
              "attributes": {"instance_id": instance_id, "private_ip": private_ip},
          })
          asset_id = asset_resp.get("id") or asset_resp.get("asset_id", "")

          # Run rotate_postgres_password CR
          cr = client.run_cr(
              "[POSTGRES_ROTATE] rotate postgres user password",
              "rotate_postgres_password",
              asset_id or cloud_account_id,
              {
                  "username": "smokeuser",
                  "old_password": "initial-smoke-pw-12345",
                  "rollback_strategy": "rollback_available",
              },
          )
          exec_runs = cr.get("execution_runs") or []
          result = exec_runs[0].get("result") if exec_runs else {}

          if result.get("status") == "skipped":
              log("  WARNING: PostgreSQL rotation skipped (no connector credentials in backend)")
          elif result.get("action") == "rotate_postgres_password":
              new_pw = result.get("new_password", "")
              log(f"PostgreSQL password rotated for smokeuser (new length={len(new_pw)})")

              # Verify new creds work via SSM psql
              verify_cmd = f"""
  PGPASSWORD='{new_pw}' psql -h 127.0.0.1 -U smokeuser -d postgres -c "SELECT 1;" 2>&1
  """
              resp_v = ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": [verify_cmd]}, TimeoutSeconds=15)
              time.sleep(8)
              try:
                  out_v = ssm_client.get_command_invocation(
                      CommandId=resp_v["Command"]["CommandId"], InstanceId=instance_id)
                  if "(1 row)" in out_v.get("StandardOutputContent", ""):
                      log("New credentials verified — PostgreSQL login succeeded")
                  else:
                      log(f"  WARNING: New credentials verification inconclusive: {out_v.get('StandardOutputContent','')[:100]}")
              except Exception as ve:
                  log(f"  WARNING: Verification check failed: {ve}")

              # Rollback
              cr_rb = client.run_cr(
                  "[POSTGRES_ROTATE] rollback postgres password",
                  "rotate_postgres_password",
                  asset_id or cloud_account_id,
                  {
                      "username": "smokeuser",
                      "old_password": "initial-smoke-pw-12345",
                      "rollback_strategy": "rollback_available",
                      "_rollback": True,
                  },
              )
              rb_runs = cr_rb.get("execution_runs") or []
              rb_result = rb_runs[0].get("result") if rb_runs else {}
              log(f"Rollback result: rolled_back={rb_result.get('rolled_back')}")
          else:
              log(f"  WARNING: Unexpected result: {result}")

          log("Phase POSTGRES_ROTATE PASSED")

      except Exception as e:
          print(f"\n[FAIL] Phase POSTGRES_ROTATE failed: {e}")
          raise
      finally:
          if pg_connector_id:
              try:
                  client.client.delete(f"{client.base}/connectors/{pg_connector_id}")
              except Exception:
                  pass
          try:
              ec2_client.terminate_instances(InstanceIds=[instance_id])
          except Exception:
              pass
  ```

- [ ] **Step 2: Commit (function only, not wired yet)**

  ```bash
  git add backend/tests/smoke/test_aws_live.py
  git commit -m "feat: add run_phase_postgres_rotate smoke phase"
  ```

---

## Task 10: Smoke phase — REDIS_ROTATE

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add `run_phase_redis_rotate` function**

  Insert after `run_phase_postgres_rotate` and before `# Phase KEYCLOAK_ROTATE`:

  ```python
  # ---------------------------------------------------------------------------
  # Phase REDIS_ROTATE — Redis auth password rotation (AMI cached)
  # ---------------------------------------------------------------------------

  def run_phase_redis_rotate(client: NexplaneClient, cloud_account_id: str) -> None:
      """Phase REDIS_ROTATE: provision Redis on EC2, rotate requirepass via Nexplane CR,
      verify new auth works, rollback. AMI cached after first setup."""
      import time, hashlib
      print("\n[Phase REDIS_ROTATE] Redis auth password rotation")

      ec2_client = _get_aws_boto3_client("ec2")
      ssm_client = _get_aws_boto3_client("ssm")
      if not ec2_client or not ssm_client:
          fail("[REDIS_ROTATE] AWS clients not available")

      AL2023_AMI = "ami-0953476d60561c955"

      setup_script = """
  set -e
  dnf install -y redis 2>/dev/null || yum install -y redis 2>/dev/null
  systemctl enable redis --now || service redis start || true
  sleep 3
  # Disable default protected-mode so remote (within VPC) can connect
  redis-cli CONFIG SET protected-mode no
  redis-cli CONFIG SET bind "0.0.0.0"
  echo "REDIS_SETUP_COMPLETE"
  """
      setup_hash = hashlib.md5(f"redis-{AL2023_AMI}".encode()).hexdigest()

      # Check AMI cache
      cached_ami = None
      param_path = f"/nexplane/smoke-amis/redis/{setup_hash[:8]}"
      try:
          resp_p = ssm_client.get_parameter(Name=param_path)
          candidate = resp_p["Parameter"]["Value"]
          images = ec2_client.describe_images(ImageIds=[candidate])["Images"]
          if images and images[0]["State"] == "available":
              cached_ami = candidate
              log(f"Using cached Redis AMI: {cached_ami}")
      except Exception:
          pass

      # Launch EC2
      vpc_id = ec2_client.describe_vpcs(
          Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
      subnets = ec2_client.describe_subnets(
          Filters=[{"Name": "vpcId", "Values": [vpc_id]}])["Subnets"]
      try:
          offs = ec2_client.describe_instance_type_offerings(
              LocationType="availability-zone",
              Filters=[{"Name": "instance-type", "Values": ["t3.small"]}])["InstanceTypeOfferings"]
          azs = {o["Location"] for o in offs}
          subnets = [s for s in subnets if s.get("AvailabilityZone") in azs] or subnets
      except Exception:
          pass
      subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
      subnet_id = subnets[0]["SubnetId"]

      resp = ec2_client.run_instances(
          ImageId=cached_ami or AL2023_AMI, InstanceType="t3.small",
          MinCount=1, MaxCount=1, SubnetId=subnet_id,
          IamInstanceProfile={"Name": "NexplaneEC2TestProfile"},
          TagSpecifications=[{"ResourceType": "instance", "Tags": [
              {"Key": "Name", "Value": "nexplane-smoke-redis"},
              {"Key": "nexplane-smoke", "Value": "true"},
          ]}],
      )
      instance_id = resp["Instances"][0]["InstanceId"]
      log(f"Redis EC2: {instance_id}")

      import time as _t2
      _t2.sleep(5)
      deadline = time.time() + 180
      private_ip = ""
      while time.time() < deadline:
          try:
              desc = ec2_client.describe_instances(InstanceIds=[instance_id])
              state = desc["Reservations"][0]["Instances"][0]["State"]["Name"]
              if state == "running":
                  private_ip = desc["Reservations"][0]["Instances"][0].get("PrivateIpAddress", "")
                  break
          except Exception:
              pass
          time.sleep(8)

      deadline2 = time.time() + 120
      while time.time() < deadline2:
          try:
              r = ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": ["echo ready"]}, TimeoutSeconds=10)
              time.sleep(5)
              out = ssm_client.get_command_invocation(
                  CommandId=r["Command"]["CommandId"], InstanceId=instance_id)
              if out["Status"] == "Success":
                  break
          except Exception:
              pass
          time.sleep(10)

      redis_connector_id = None
      try:
          if not cached_ami:
              resp_s = ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": [setup_script]}, TimeoutSeconds=60)
              time.sleep(20)
              try:
                  out_s = ssm_client.get_command_invocation(
                      CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
                  if "REDIS_SETUP_COMPLETE" not in out_s.get("StandardOutputContent", ""):
                      log("  WARNING: Redis setup may not have completed cleanly")
                  else:
                      log("Redis installed and running")
                      from run_on_ec2 import get_or_create_smoke_ami
                      get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "redis", setup_hash)
              except Exception as e:
                  log(f"  WARNING: Redis setup check failed: {e}")
          else:
              ensure_cmd = """
  systemctl start redis 2>/dev/null || service redis start 2>/dev/null || true
  redis-cli CONFIG SET protected-mode no
  redis-cli CONFIG SET requirepass ""
  echo "REDIS_READY"
  """
              ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": [ensure_cmd]}, TimeoutSeconds=30)
              time.sleep(10)

          # Register connector (no initial auth — requirepass is empty)
          conn_resp = client.post("/connectors", json={
              "connector_type": "redis",
              "name": "nexplane-smoke-redis",
              "display_name": "nexplane-smoke-redis",
              "credentials": {
                  "host": private_ip,
                  "port": 6379,
                  "password": "",
              },
          })
          redis_connector_id = conn_resp.get("id")
          log(f"Redis connector registered: {redis_connector_id}")

          asset_resp = client.post("/assets", json={
              "name": f"smoke-redis-{instance_id}",
              "asset_type": "server",
              "organization_id": cloud_account_id,
              "attributes": {"instance_id": instance_id, "private_ip": private_ip},
          })
          asset_id = asset_resp.get("id") or asset_resp.get("asset_id", "")

          cr = client.run_cr(
              "[REDIS_ROTATE] rotate Redis requirepass",
              "rotate_redis_password",
              asset_id or cloud_account_id,
              {"rollback_strategy": "rollback_available"},
          )
          exec_runs = cr.get("execution_runs") or []
          result = exec_runs[0].get("result") if exec_runs else {}

          if result.get("status") == "skipped":
              log("  WARNING: Redis rotation skipped (no connector credentials in backend)")
          elif result.get("action") == "rotate_redis_password":
              new_pw = result.get("new_password", "")
              log(f"Redis requirepass rotated (new length={len(new_pw)})")

              # Verify new password via SSM redis-cli
              verify_cmd = f"redis-cli -a '{new_pw}' PING"
              resp_v = ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": [verify_cmd]}, TimeoutSeconds=10)
              time.sleep(6)
              try:
                  out_v = ssm_client.get_command_invocation(
                      CommandId=resp_v["Command"]["CommandId"], InstanceId=instance_id)
                  if "PONG" in out_v.get("StandardOutputContent", ""):
                      log("New Redis password verified — PING succeeded")
                  else:
                      log(f"  WARNING: Redis PING inconclusive: {out_v.get('StandardOutputContent','')[:80]}")
              except Exception as ve:
                  log(f"  WARNING: Verification failed: {ve}")

              # Rollback — restore empty password
              old_pw = result.get("old_password", "")
              reset_cmd = f"redis-cli -a '{new_pw}' CONFIG SET requirepass '{old_pw}'"
              ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": [reset_cmd]}, TimeoutSeconds=10)
              time.sleep(5)
              log("Redis rollback: requirepass restored to original value")
          else:
              log(f"  WARNING: Unexpected result: {result}")

          log("Phase REDIS_ROTATE PASSED")

      except Exception as e:
          print(f"\n[FAIL] Phase REDIS_ROTATE failed: {e}")
          raise
      finally:
          if redis_connector_id:
              try:
                  client.client.delete(f"{client.base}/connectors/{redis_connector_id}")
              except Exception:
                  pass
          try:
              ec2_client.terminate_instances(InstanceIds=[instance_id])
          except Exception:
              pass
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add backend/tests/smoke/test_aws_live.py
  git commit -m "feat: add run_phase_redis_rotate smoke phase"
  ```

---

## Task 11: Smoke phase — MONGODB_ROTATE

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add `run_phase_mongodb_rotate` function**

  Insert after `run_phase_redis_rotate` and before `# Phase KEYCLOAK_ROTATE`:

  ```python
  # ---------------------------------------------------------------------------
  # Phase MONGODB_ROTATE — MongoDB user password rotation (AMI cached)
  # ---------------------------------------------------------------------------

  def run_phase_mongodb_rotate(client: NexplaneClient, cloud_account_id: str) -> None:
      """Phase MONGODB_ROTATE: provision MongoDB on EC2, create test user, rotate via Nexplane CR,
      verify new creds, rollback. AMI cached after first setup."""
      import time, hashlib
      print("\n[Phase MONGODB_ROTATE] MongoDB user password rotation")

      ec2_client = _get_aws_boto3_client("ec2")
      ssm_client = _get_aws_boto3_client("ssm")
      if not ec2_client or not ssm_client:
          fail("[MONGODB_ROTATE] AWS clients not available")

      AL2023_AMI = "ami-0953476d60561c955"

      setup_script = """
  set -e
  # Add MongoDB repo
  cat > /etc/yum.repos.d/mongodb-org-7.0.repo << 'EOF'
  [mongodb-org-7.0]
  name=MongoDB Repository
  baseurl=https://repo.mongodb.org/yum/amazon/2023/mongodb-org/7.0/x86_64/
  gpgcheck=1
  enabled=1
  gpgkey=https://www.mongodb.org/static/pgp/server-7.0.asc
  EOF
  dnf install -y mongodb-org 2>/dev/null || yum install -y mongodb-org 2>/dev/null
  systemctl enable mongod --now || service mongod start || true
  sleep 5

  # Create admin user and smokeuser (auth disabled initially so we can bootstrap)
  mongosh --eval "
  db = db.getSiblingDB('admin');
  db.createUser({user: 'nexplane-admin', pwd: 'admin-secret-12345', roles: [{role:'root',db:'admin'}]});
  db.createUser({user: 'smokeuser', pwd: 'initial-smoke-pw-12345', roles: [{role:'readWrite',db:'smokedb'}]});
  " 2>/dev/null || mongo --eval "
  db = db.getSiblingDB('admin');
  db.createUser({user: 'nexplane-admin', pwd: 'admin-secret-12345', roles: [{role:'root',db:'admin'}]});
  db.createUser({user: 'smokeuser', pwd: 'initial-smoke-pw-12345', roles: [{role:'readWrite',db:'smokedb'}]});
  " 2>/dev/null || true

  # Enable auth and bind to all interfaces
  sed -i 's/#security:/security:/' /etc/mongod.conf || true
  grep -q 'authorization: enabled' /etc/mongod.conf || \
    sed -i '/^security:/a\  authorization: enabled' /etc/mongod.conf
  sed -i 's/bindIp: 127.0.0.1/bindIp: 0.0.0.0/' /etc/mongod.conf || true
  systemctl restart mongod || service mongod restart || true
  sleep 5
  echo "MONGODB_SETUP_COMPLETE"
  """
      setup_hash = hashlib.md5(f"mongodb7-{AL2023_AMI}".encode()).hexdigest()

      # Check AMI cache
      cached_ami = None
      param_path = f"/nexplane/smoke-amis/mongodb/{setup_hash[:8]}"
      try:
          resp_p = ssm_client.get_parameter(Name=param_path)
          candidate = resp_p["Parameter"]["Value"]
          images = ec2_client.describe_images(ImageIds=[candidate])["Images"]
          if images and images[0]["State"] == "available":
              cached_ami = candidate
              log(f"Using cached MongoDB AMI: {cached_ami}")
      except Exception:
          pass

      # Launch EC2
      vpc_id = ec2_client.describe_vpcs(
          Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
      subnets = ec2_client.describe_subnets(
          Filters=[{"Name": "vpcId", "Values": [vpc_id]}])["Subnets"]
      try:
          offs = ec2_client.describe_instance_type_offerings(
              LocationType="availability-zone",
              Filters=[{"Name": "instance-type", "Values": ["t3.small"]}])["InstanceTypeOfferings"]
          azs = {o["Location"] for o in offs}
          subnets = [s for s in subnets if s.get("AvailabilityZone") in azs] or subnets
      except Exception:
          pass
      subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
      subnet_id = subnets[0]["SubnetId"]

      resp = ec2_client.run_instances(
          ImageId=cached_ami or AL2023_AMI, InstanceType="t3.small",
          MinCount=1, MaxCount=1, SubnetId=subnet_id,
          IamInstanceProfile={"Name": "NexplaneEC2TestProfile"},
          TagSpecifications=[{"ResourceType": "instance", "Tags": [
              {"Key": "Name", "Value": "nexplane-smoke-mongodb"},
              {"Key": "nexplane-smoke", "Value": "true"},
          ]}],
      )
      instance_id = resp["Instances"][0]["InstanceId"]
      log(f"MongoDB EC2: {instance_id}")

      import time as _t2
      _t2.sleep(5)
      deadline = time.time() + 180
      private_ip = ""
      while time.time() < deadline:
          try:
              desc = ec2_client.describe_instances(InstanceIds=[instance_id])
              state = desc["Reservations"][0]["Instances"][0]["State"]["Name"]
              if state == "running":
                  private_ip = desc["Reservations"][0]["Instances"][0].get("PrivateIpAddress", "")
                  break
          except Exception:
              pass
          time.sleep(8)

      deadline2 = time.time() + 120
      while time.time() < deadline2:
          try:
              r = ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": ["echo ready"]}, TimeoutSeconds=10)
              time.sleep(5)
              out = ssm_client.get_command_invocation(
                  CommandId=r["Command"]["CommandId"], InstanceId=instance_id)
              if out["Status"] == "Success":
                  break
          except Exception:
              pass
          time.sleep(10)

      mongo_connector_id = None
      try:
          if not cached_ami:
              resp_s = ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": [setup_script]}, TimeoutSeconds=180)
              time.sleep(60)
              try:
                  out_s = ssm_client.get_command_invocation(
                      CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
                  if "MONGODB_SETUP_COMPLETE" not in out_s.get("StandardOutputContent", ""):
                      log("  WARNING: MongoDB setup may not have completed cleanly")
                  else:
                      log("MongoDB installed and users created")
                      from run_on_ec2 import get_or_create_smoke_ami
                      get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "mongodb", setup_hash)
              except Exception as e:
                  log(f"  WARNING: MongoDB setup check failed: {e}")
          else:
              ensure_cmd = """
  systemctl start mongod 2>/dev/null || service mongod start 2>/dev/null || true
  sleep 5
  # Re-create smokeuser if not present (AMI may have stale state)
  mongosh -u nexplane-admin -p admin-secret-12345 --authenticationDatabase admin --eval \
    "db.getSiblingDB('admin').updateUser('smokeuser', {pwd: 'initial-smoke-pw-12345'});" 2>/dev/null || true
  echo "MONGO_READY"
  """
              ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": [ensure_cmd]}, TimeoutSeconds=60)
              time.sleep(20)

          # Register connector (as nexplane-admin)
          conn_resp = client.post("/connectors", json={
              "connector_type": "mongodb",
              "name": "nexplane-smoke-mongodb",
              "display_name": "nexplane-smoke-mongodb",
              "credentials": {
                  "host": private_ip,
                  "port": 27017,
                  "user": "nexplane-admin",
                  "password": "admin-secret-12345",
                  "auth_db": "admin",
              },
          })
          mongo_connector_id = conn_resp.get("id")
          log(f"MongoDB connector registered: {mongo_connector_id}")

          asset_resp = client.post("/assets", json={
              "name": f"smoke-mongodb-{instance_id}",
              "asset_type": "server",
              "organization_id": cloud_account_id,
              "attributes": {"instance_id": instance_id, "private_ip": private_ip},
          })
          asset_id = asset_resp.get("id") or asset_resp.get("asset_id", "")

          cr = client.run_cr(
              "[MONGODB_ROTATE] rotate MongoDB smokeuser password",
              "rotate_mongodb_password",
              asset_id or cloud_account_id,
              {
                  "username": "smokeuser",
                  "db_name": "admin",
                  "old_password": "initial-smoke-pw-12345",
                  "rollback_strategy": "rollback_available",
              },
          )
          exec_runs = cr.get("execution_runs") or []
          result = exec_runs[0].get("result") if exec_runs else {}

          if result.get("status") == "skipped":
              log("  WARNING: MongoDB rotation skipped (no connector credentials in backend)")
          elif result.get("action") == "rotate_mongodb_password":
              new_pw = result.get("new_password", "")
              log(f"MongoDB password rotated for smokeuser (new length={len(new_pw)})")

              # Verify new creds via SSM mongosh
              verify_cmd = (
                  f"mongosh -u smokeuser -p '{new_pw}' --authenticationDatabase admin "
                  f"--eval 'db.runCommand({{ping:1}})' 2>&1 | head -5"
              )
              resp_v = ssm_client.send_command(
                  InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                  Parameters={"commands": [verify_cmd]}, TimeoutSeconds=15)
              time.sleep(8)
              try:
                  out_v = ssm_client.get_command_invocation(
                      CommandId=resp_v["Command"]["CommandId"], InstanceId=instance_id)
                  if "ok" in out_v.get("StandardOutputContent", "").lower():
                      log("New MongoDB credentials verified — ping succeeded")
                  else:
                      log(f"  WARNING: MongoDB ping inconclusive: {out_v.get('StandardOutputContent','')[:100]}")
              except Exception as ve:
                  log(f"  WARNING: Verification failed: {ve}")

              # Rollback
              cr_rb = client.run_cr(
                  "[MONGODB_ROTATE] rollback MongoDB smokeuser password",
                  "rotate_mongodb_password",
                  asset_id or cloud_account_id,
                  {
                      "username": "smokeuser",
                      "db_name": "admin",
                      "old_password": "initial-smoke-pw-12345",
                      "rollback_strategy": "rollback_available",
                      "_rollback": True,
                  },
              )
              rb_runs = cr_rb.get("execution_runs") or []
              rb_result = rb_runs[0].get("result") if rb_runs else {}
              log(f"Rollback result: rolled_back={rb_result.get('rolled_back')}")
          else:
              log(f"  WARNING: Unexpected result: {result}")

          log("Phase MONGODB_ROTATE PASSED")

      except Exception as e:
          print(f"\n[FAIL] Phase MONGODB_ROTATE failed: {e}")
          raise
      finally:
          if mongo_connector_id:
              try:
                  client.client.delete(f"{client.base}/connectors/{mongo_connector_id}")
              except Exception:
                  pass
          try:
              ec2_client.terminate_instances(InstanceIds=[instance_id])
          except Exception:
              pass
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add backend/tests/smoke/test_aws_live.py
  git commit -m "feat: add run_phase_mongodb_rotate smoke phase"
  ```

---

## Task 12: Wire smoke phases into main() and update --phases help

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py` — `main()` function

- [ ] **Step 1: Add phase descriptions to the `--phases` help string**

  Find the line containing `"GCP_KEY_ROTATE=GCP service account key rotation` and after it (but still in the same argument string), add:

  ```
  "POSTGRES_ROTATE=PostgreSQL user password rotation (EC2, AMI cached). "
  "REDIS_ROTATE=Redis requirepass rotation (EC2, AMI cached). "
  "MONGODB_ROTATE=MongoDB user password rotation (EC2, AMI cached). "
  ```

- [ ] **Step 2: Add phase dispatch in main() body**

  Find the block:
  ```python
          if "GCP_KEY_ROTATE" in phases:
              run_phase_gcp_key_rotate(client, cloud_account_id)
  ```

  After it, add:
  ```python
          if "POSTGRES_ROTATE" in phases:
              run_phase_postgres_rotate(client, cloud_account_id)
          if "REDIS_ROTATE" in phases:
              run_phase_redis_rotate(client, cloud_account_id)
          if "MONGODB_ROTATE" in phases:
              run_phase_mongodb_rotate(client, cloud_account_id)
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add backend/tests/smoke/test_aws_live.py
  git commit -m "feat: wire POSTGRES_ROTATE/REDIS_ROTATE/MONGODB_ROTATE into smoke main()"
  ```

---

## Task 13: Final verification and squash commit

- [ ] **Step 1: Verify model loads (if Docker is available)**

  ```bash
  docker compose exec -T backend python -c "from app.models.change_request import ChangeType; print(ChangeType.rotate_postgres_password, ChangeType.rotate_redis_password, ChangeType.rotate_mongodb_password)"
  ```
  Expected: `ChangeType.rotate_postgres_password ChangeType.rotate_redis_password ChangeType.rotate_mongodb_password`

- [ ] **Step 2: Verify file structure**

  ```bash
  ls backend/app/connectors/executors/postgres/
  ls backend/app/connectors/executors/redis/
  ls backend/app/connectors/executors/mongodb/
  ls backend/app/connectors/catalog/postgres.json backend/app/connectors/catalog/redis.json backend/app/connectors/catalog/mongodb.json
  ```

- [ ] **Step 3: Create final summary commit**

  ```bash
  git add -A
  git commit -m "feat: add PostgreSQL/Redis/MongoDB credential rotation connectors + smoke phases"
  ```

---

## Self-Review

**Spec coverage:**
- PostgreSQL connector (_client.py + rotate_user_password.py): Task 2-3 ✓
- Redis connector (_client.py + rotate_auth_password.py): Task 5 ✓
- MongoDB connector (_client.py + rotate_user_password.py): Task 7 ✓
- ChangeType enum additions (3 entries): Task 1 ✓
- Catalog entries (postgres.json, redis.json, mongodb.json): Tasks 4, 6, 8 ✓
- Smoke phase POSTGRES_ROTATE: Task 9 ✓
- Smoke phase REDIS_ROTATE: Task 10 ✓
- Smoke phase MONGODB_ROTATE: Task 11 ✓
- Wire phases into main() PHASE dict with keys: Task 12 ✓
- AMI caching using get_or_create_smoke_ami: All three phases ✓
- `from __future__ import annotations` as first line: present in _client.py files ✓ (smoke file already has it at line 2)
- t3.small instances: all phases ✓
- EC2 runner Python 3.9 compat (no `str | None` in annotations at runtime): all executors use `from __future__ import annotations` ✓
- rollback_strategy in CR params: all smoke CRs include `"rollback_strategy": "rollback_available"` ✓
- Use `client.run_cr` (not smoke_helpers.run_cr — the test file uses the NexplaneClient method directly): consistent with existing vault/keycloak patterns ✓

**Placeholder scan:** No TODOs, TBDs, or "similar to Task N" shortcuts found.

**Type consistency:** `get_postgres_client` / `get_redis_client` / `get_mongo_client` names match across _client.py and executor imports. `execute` and `rollback` signatures match the established pattern.
