# PostgreSQL Connector

The PostgreSQL connector uses `psycopg2` to execute database-level security operations against PostgreSQL 12+ instances.

## Credential fields

| Field | Required | Description |
|---|---|---|
| `host` | Yes | PostgreSQL host |
| `port` | Yes | PostgreSQL port (default: `5432`) |
| `database` | Yes | Database to connect to for administrative operations (typically `postgres`) |
| `username` | Yes | Nexplane service account username |
| `password` | Yes | Service account password |
| `ssl_mode` | No | `disable`, `require`, `verify-ca`, or `verify-full`. Default: `require` |

### Required privileges

The Nexplane service account must be a superuser or have the `CREATEROLE` privilege to manage other roles. For privilege revocation operations, it must have the target database's privileges granted with `GRANT OPTION`.

## Supported change types

### `lock_role`

Prevents a PostgreSQL role from logging in by setting `NOLOGIN`.

| Parameter | Type | Description |
|---|---|---|
| `role_name` | string | Name of the PostgreSQL role to lock |

**Rollback**: Restores the `LOGIN` attribute if it was set before locking.

---

### `revoke_privilege`

Revokes a privilege from a role on a specific object.

| Parameter | Type | Description |
|---|---|---|
| `role_name` | string | Role to revoke the privilege from |
| `privilege` | string | Privilege to revoke (e.g. `SELECT`, `INSERT`, `ALL`) |
| `object_type` | enum: `table` \| `schema` \| `database` | Object type |
| `object_name` | string | Name of the object |
| `schema` | string | Schema name (required when `object_type` is `table`) |

**Rollback**: Re-grants the privilege with the same options that were present before revocation.
