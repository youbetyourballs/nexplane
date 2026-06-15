#!/usr/bin/env python3
"""Grant a collaborator VPN access to the dev instance via Tailscale.

This is the repeatable, audited way to give an "interested party" access to the
Nexplane dev instance. It reuses the Tailscale credentials already stored on the
Tailscale connector in the database (decrypting them with the configured secret
backend) and mints a *fresh, per-person* Tailscale auth key. The collaborator
runs the printed `tailscale up` command on their own device to join the tailnet
and reach the dev instance over the Tailscale overlay network.

Design choices (secure defaults for a human collaborator's device):
  * single-use   (reusable=False)  -> one key enrolls one device, no sharing
  * non-ephemeral (ephemeral=False) -> the device persists across reconnects
  * pre-authorized                  -> no manual approval step in the admin console
  * tagged                          -> access is governed by ACLs on the tag
  * expiring     (default 90 days)  -> access is time-boxed, not forever
  * described    ("VPN access for <name> ...") -> every key is attributable

Run this ON the dev instance (or anywhere DATABASE_URL points at the dev DB and
SECRET_KEY / SECRET_BACKEND match), from the backend/ directory:

    python -m scripts.grant_vpn_access "Lucas Nelson"
    python -m scripts.grant_vpn_access "Lucas Nelson" --email lucas@example.com
    python -m scripts.grant_vpn_access "Lucas Nelson" --expiry-days 30 --tag tag:collaborator
    python -m scripts.grant_vpn_access --list-tailnet   # show current nodes (find the dev target)

Minting new keys requires OAuth client credentials (oauth_client_id +
oauth_client_secret) or a Tailscale API access token (api_key) on the connector.
A single stored reusable `auth_key` cannot be used to issue per-person keys --
sharing one key across people defeats the point and is refused.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

import httpx

TAILSCALE_API_BASE = "https://api.tailscale.com/api/v2"


class GrantError(Exception):
    """A user-actionable failure (printed cleanly, no traceback)."""


async def _load_credentials(connector_ref: str | None, org_id: str | None):
    """Find the Tailscale connector and return (connector, decrypted credentials)."""
    # Imported lazily so the tool's pure logic (and its tests) don't require the
    # full app/pydantic/db stack just to build a key body.
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.connector import Connector, ConnectorType
    from app.models.connector_credential import ConnectorCredential
    from app.services.secret_backend_factory import get_secret_backend

    async with AsyncSessionLocal() as db:
        stmt = select(Connector).where(Connector.connector_type == ConnectorType.tailscale)
        if org_id:
            stmt = stmt.where(Connector.organization_id == org_id)
        connectors = (await db.execute(stmt)).scalars().all()

        if connector_ref:
            ref = connector_ref.lower()
            connectors = [c for c in connectors if str(c.id) == ref or c.name.lower() == ref]

        if not connectors:
            raise GrantError(
                "No Tailscale connector found"
                + (f" matching {connector_ref!r}" if connector_ref else "")
                + ". Configure one in the platform first (Connectors -> Add -> Tailscale)."
            )
        if len(connectors) > 1:
            listing = "\n".join(f"  - {c.name}  (id={c.id})" for c in connectors)
            raise GrantError(
                "Multiple Tailscale connectors found; pick one with --connector "
                f"<name|id>:\n{listing}"
            )

        connector = connectors[0]
        cred_row = (
            await db.execute(
                select(ConnectorCredential).where(
                    ConnectorCredential.connector_id == connector.id
                )
            )
        ).scalar_one_or_none()
        if not cred_row:
            raise GrantError(
                f"Tailscale connector {connector.name!r} has no credentials configured."
            )
        creds = get_secret_backend().decrypt_json(cred_row.credentials_encrypted)
        return connector, creds


async def _oauth_token(client: httpx.AsyncClient, creds: dict) -> str:
    resp = await client.post(
        f"{TAILSCALE_API_BASE}/oauth/token",
        data={
            "client_id": creds["oauth_client_id"],
            "client_secret": creds["oauth_client_secret"],
            "grant_type": "client_credentials",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _auth_mode(creds: dict) -> str:
    if creds.get("oauth_client_id") and creds.get("oauth_client_secret"):
        return "oauth"
    if creds.get("api_key") or creds.get("token"):
        return "api_key"
    if creds.get("auth_key"):
        raise GrantError(
            "This connector only has a single reusable auth_key stored. That key "
            "cannot mint per-person keys, and sharing one key across collaborators "
            "is insecure. Add OAuth client credentials (oauth_client_id + "
            "oauth_client_secret) or an api_key to the Tailscale connector to issue "
            "individual keys."
        )
    raise GrantError("Tailscale connector has no usable credentials.")


async def _api_headers(client: httpx.AsyncClient, creds: dict, mode: str) -> dict:
    if mode == "oauth":
        token = await _oauth_token(client, creds)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    # api_key / personal access token -> HTTP basic auth, key as username
    import base64

    key = creds.get("api_key") or creds["token"]
    basic = base64.b64encode(f"{key}:".encode()).decode()
    return {"Authorization": f"Basic {basic}", "Content-Type": "application/json"}


def build_description(name: str, email: str | None, issued_by: str) -> str:
    description = f"VPN access for {name}"
    if email:
        description += f" <{email}>"
    return (
        description
        + f" - issued by {issued_by} via grant_vpn_access on "
        + f"{datetime.now(timezone.utc):%Y-%m-%d}"
    )


def build_key_body(
    *, tag: str, expiry_days: int, reusable: bool, ephemeral: bool, description: str
) -> dict:
    return {
        "capabilities": {
            "devices": {
                "create": {
                    "reusable": reusable,
                    "ephemeral": ephemeral,
                    "preauthorized": True,
                    "tags": [tag],
                }
            }
        },
        "expirySeconds": expiry_days * 86400,
        "description": description,
    }


async def create_key(
    creds: dict,
    *,
    name: str,
    email: str | None,
    tag: str,
    expiry_days: int,
    reusable: bool,
    ephemeral: bool,
    issued_by: str,
    list_only: bool,
    target_filter: str | None,
) -> dict:
    mode = _auth_mode(creds)
    tailnet = creds.get("tailnet") or creds.get("org") or "-"

    async with httpx.AsyncClient(timeout=30) as client:
        headers = await _api_headers(client, creds, mode)

        if list_only:
            resp = await client.get(
                f"{TAILSCALE_API_BASE}/tailnet/{tailnet}/devices", headers=headers
            )
            resp.raise_for_status()
            return {"devices": resp.json().get("devices", [])}

        body = build_key_body(
            tag=tag,
            expiry_days=expiry_days,
            reusable=reusable,
            ephemeral=ephemeral,
            description=build_description(name, email, issued_by),
        )
        resp = await client.post(
            f"{TAILSCALE_API_BASE}/tailnet/{tailnet}/keys", headers=headers, json=body
        )
        if resp.status_code >= 400:
            raise GrantError(
                f"Tailscale API rejected key creation ({resp.status_code}): {resp.text.strip()}\n"
                f"Common cause: the tag {tag!r} must exist in your tailnet ACL and be owned by "
                "this OAuth client / key. See docs/connectors/tailscale.md."
            )

        result = resp.json()
        # Best-effort: surface candidate dev-instance nodes to share with the collaborator.
        targets = []
        try:
            dresp = await client.get(
                f"{TAILSCALE_API_BASE}/tailnet/{tailnet}/devices", headers=headers
            )
            if dresp.status_code < 400:
                for d in dresp.json().get("devices", []):
                    hay = f"{d.get('hostname', '')} {d.get('name', '')}".lower()
                    if target_filter and target_filter.lower() not in hay:
                        continue
                    targets.append(d)
        except httpx.HTTPError:
            pass
        result["_tailnet"] = tailnet
        result["_targets"] = targets
        return result


def _print_devices(devices: list) -> None:
    if not devices:
        print("  (no devices in tailnet)")
        return
    for d in devices:
        addrs = ", ".join(d.get("addresses", []))
        print(f"  - {d.get('name', d.get('hostname', '?'))}  {addrs}  (user={d.get('user', '-')})")


def _print_grant(name: str, key: dict, tag: str, expiry_days: int) -> None:
    auth_key = key.get("key", "")
    tailnet = key.get("_tailnet", "-")
    print("\n" + "=" * 72)
    print(f"  VPN access granted to: {name}")
    print("=" * 72)
    print(f"  Tailnet : {tailnet}")
    print(f"  Tag     : {tag}")
    print(f"  Expires : in {expiry_days} days  ({key.get('expires', '?')})")
    print(f"  Key ID  : {key.get('id', '?')}")
    print("\n  Send the collaborator the auth key below (one-time, single-use):\n")
    print(f"    {auth_key}\n")
    print("  They install Tailscale (https://tailscale.com/download) and run:\n")
    print(f"    tailscale up --authkey {auth_key}\n")
    targets = key.get("_targets") or []
    if targets:
        print("  Dev instance node(s) they can then reach over Tailscale:")
        _print_devices(targets)
        print()
    print("  Revoke later with: tailscale.com/admin/machines -> delete the device,")
    print("  or expire the key at tailscale.com/admin/settings/keys.")
    print("=" * 72 + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Issue a per-collaborator Tailscale auth key for dev-instance VPN access."
    )
    p.add_argument("name", nargs="?", help='Collaborator full name, e.g. "Lucas Nelson"')
    p.add_argument("--email", help="Collaborator email (recorded in the key description)")
    p.add_argument("--connector", help="Tailscale connector name or id (if more than one exists)")
    p.add_argument("--org-id", help="Organization id (if the DB has more than one org)")
    p.add_argument("--tag", default="tag:collaborator", help="ACL tag for the device (default: tag:collaborator)")
    p.add_argument("--expiry-days", type=int, default=90, help="Key/device key expiry in days (default: 90)")
    p.add_argument("--reusable", action="store_true", help="Make the key reusable (default: single-use)")
    p.add_argument("--ephemeral", action="store_true", help="Make the device ephemeral (default: persistent)")
    p.add_argument("--target-filter", help="Substring to match the dev-instance node name to show")
    p.add_argument("--list-tailnet", action="store_true", help="List current tailnet devices and exit")
    p.add_argument("--issued-by", default="ops", help="Who is issuing this grant (recorded in description)")
    return p.parse_args(argv)


async def main_async(argv: list[str]) -> int:
    args = parse_args(argv)
    if not args.list_tailnet and not args.name:
        raise GrantError('Provide the collaborator name, e.g. grant_vpn_access "Lucas Nelson"')

    _, creds = await _load_credentials(args.connector, args.org_id)
    result = await create_key(
        creds,
        name=args.name or "",
        email=args.email,
        tag=args.tag,
        expiry_days=args.expiry_days,
        reusable=args.reusable,
        ephemeral=args.ephemeral,
        issued_by=args.issued_by,
        list_only=args.list_tailnet,
        target_filter=args.target_filter,
    )

    if args.list_tailnet:
        print("Current tailnet devices:")
        _print_devices(result["devices"])
        return 0

    _print_grant(args.name, result, args.tag, args.expiry_days)
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async(sys.argv[1:]))
    except GrantError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
