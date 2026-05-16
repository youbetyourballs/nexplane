# LDAP Connector

The LDAP connector uses `python-ldap` to perform directory operations against any RFC 4511-compliant LDAP server, including Active Directory, OpenLDAP, and FreeIPA.

## Credential fields

| Field | Required | Description |
|---|---|---|
| `host` | Yes | LDAP server hostname or IP address |
| `port` | Yes | LDAP port — typically `389` (LDAP) or `636` (LDAPS) |
| `bind_dn` | Yes | Distinguished name of the bind account (e.g. `cn=nexplane,ou=service-accounts,dc=example,dc=com`) |
| `bind_password` | Yes | Password for the bind account |
| `base_dn` | Yes | Base DN for search operations (e.g. `dc=example,dc=com`) |
| `use_ssl` | No | Set to `true` to use LDAPS (port 636). Default: `false` |
| `verify_ssl` | No | Set to `false` to skip certificate verification (not recommended for production). Default: `true` |

### Bind account permissions

The bind account must have:

- **Read** access to user objects within the base DN (for snapshot capture)
- **Write** access to modify the `userAccountControl` attribute (Active Directory) or `pwdAccountLockedTime` / `nsAccountLock` attribute (OpenLDAP / FreeIPA) on user objects

## Supported change types

### `disable_user`

Disables an LDAP user account, preventing authentication. The mechanism depends on the directory type:

- **Active Directory**: Sets bit 2 (`ACCOUNTDISABLE`) in `userAccountControl`
- **OpenLDAP with ppolicy**: Sets `pwdAccountLockedTime` to `000001010000Z`
- **FreeIPA / 389-DS**: Sets `nsAccountLock` to `TRUE`

| Parameter | Type | Description |
|---|---|---|
| `username` | string | sAMAccountName (AD) or uid (OpenLDAP/FreeIPA) of the user to disable |
| `directory_type` | enum: `activedirectory` \| `openldap` \| `freeipa` | Directory flavor — determines which attribute is modified |

**Rollback**: Restores the exact attribute state captured before the disable operation (re-enables the user).

!!! note "Active Directory"
    For Active Directory targets, ensure the bind account has the **Reset Password** and **Write Account Restrictions** permissions delegated on the target OU, not just generic write access.
