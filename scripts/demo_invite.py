#!/usr/bin/env python3
"""Generate a Tailscale demo invite for a named guest.

Usage: python3 scripts/demo_invite.py --name "Lucas Nelson"

Walks the full CR lifecycle via the Nexplane platform API:
  create -> plan -> submit -> approve -> execute -> poll -> print invite
"""
import argparse
import sys
import time

try:
    import httpx
except ImportError:
    sys.exit("httpx not found. Run: pip install httpx")

BASE_URL = "http://localhost:8000"
ADMIN_EMAIL = "admin@acme.example"
ADMIN_PASSWORD = "admin123"
UI_URL = "http://100.101.186.39:3000"
DEMO_EMAIL = "admin@acme.example"
DEMO_PASSWORD = "admin123"
EXPIRY_SECONDS = 2592000  # 30 days


def login(client: httpx.Client, email: str, password: str) -> str:
    resp = client.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password})
    resp.raise_for_status()
    return resp.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def create_cr(client: httpx.Client, token: str, name: str) -> str:
    resp = client.post(
        f"{BASE_URL}/change-requests",
        headers=auth_headers(token),
        json={
            "title": f"Demo access — {name}",
            "description": f"Tailscale auth key for demo guest: {name}",
            "change_type": "tailscale_generate_auth_key",
            "target_asset_ids": [],
            "desired_outcome": {
                "expiry_seconds": EXPIRY_SECONDS,
                "reusable": False,
                "ephemeral": False,
                "force_generate": True,
                "_locked_connector_id": "c471972f-c616-4611-8a3a-81fadcd5a832",
            },
        },
        timeout=30,
    )
    resp.raise_for_status()
    cr_id = resp.json()["id"]
    print(f"  Created CR: {cr_id}")
    return cr_id


def plan_cr(client: httpx.Client, token: str, cr_id: str) -> None:
    resp = client.post(f"{BASE_URL}/change-requests/{cr_id}/plan", headers=auth_headers(token), timeout=30)
    resp.raise_for_status()
    print("  Planned")


def submit_cr(client: httpx.Client, token: str, cr_id: str) -> None:
    resp = client.post(
        f"{BASE_URL}/change-requests/{cr_id}/submit-for-approval",
        headers=auth_headers(token),
        timeout=30,
    )
    resp.raise_for_status()
    print("  Submitted for approval")


def approve_cr(client: httpx.Client, token: str, cr_id: str) -> None:
    resp = client.post(
        f"{BASE_URL}/change-requests/{cr_id}/approve",
        headers=auth_headers(token),
        json={"decision": "approved", "comment": "Demo invite approved"},
        timeout=30,
    )
    resp.raise_for_status()
    print("  Approved")


def execute_cr(client: httpx.Client, token: str, cr_id: str) -> None:
    resp = client.post(f"{BASE_URL}/change-requests/{cr_id}/execute", headers=auth_headers(token), timeout=30)
    resp.raise_for_status()
    print("  Executing...")


def poll_for_completion(client: httpx.Client, token: str, cr_id: str, timeout_seconds: int = 60) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        resp = client.get(f"{BASE_URL}/change-requests/{cr_id}", headers=auth_headers(token), timeout=30)
        resp.raise_for_status()
        cr = resp.json()
        status = cr["status"]
        if status == "completed":
            return cr
        if status in ("failed", "rejected", "cancelled"):
            sys.exit(f"CR ended in terminal state: {status}")
        time.sleep(2)
    sys.exit(f"Timed out after {timeout_seconds}s waiting for CR to complete")


def extract_auth_key(cr: dict) -> str:
    for run in cr.get("execution_runs", []):
        result = run.get("result", {})
        # Result may be nested under "execution" key or directly under "steps"
        steps = result.get("steps") or result.get("execution", {}).get("steps", [])
        for step in steps:
            key = step.get("result", {}).get("auth_key", "")
            if key:
                return key
    sys.exit("Could not find auth_key in execution result. Check the CR in the UI.")


def print_invite(name: str, cr_id: str, auth_key: str) -> None:
    first = name.split()[0]
    print()
    print("=" * 60)
    print("INVITE MESSAGE (copy below this line)")
    print("=" * 60)
    print(f"""
Hi {first}! To access the Nexplane demo:

1. Install Tailscale: https://tailscale.com/download

2. After installing, connect using this key:
   tailscale up --authkey {auth_key}

   On Windows/Mac: open the Tailscale app, click "Log in",
   then paste the key above when prompted.

3. Open {UI_URL} in your browser

4. Log in with:
   Email:    {DEMO_EMAIL}
   Password: {DEMO_PASSWORD}

The key expires in 30 days. Reply here if you run into anything!
""")
    print("=" * 60)
    print(f"(Platform CR for this invite: {BASE_URL.replace('localhost', '100.101.186.39')}/change-requests/{cr_id})")


def main():
    parser = argparse.ArgumentParser(description="Generate a Tailscale demo invite")
    parser.add_argument("--name", required=True, help='Guest full name, e.g. "Lucas Nelson"')
    args = parser.parse_args()

    print(f"Generating Tailscale invite for: {args.name}")
    with httpx.Client() as client:
        print("  Logging in as admin...")
        token = login(client, ADMIN_EMAIL, ADMIN_PASSWORD)

        print("  Creating CR...")
        cr_id = create_cr(client, token, args.name)

        print("  Planning...")
        plan_cr(client, token, cr_id)

        print("  Submitting for approval...")
        submit_cr(client, token, cr_id)

        print("  Approving (admin)...")
        approve_cr(client, token, cr_id)

        print("  Executing...")
        execute_cr(client, token, cr_id)

        print("  Waiting for completion...")
        cr = poll_for_completion(client, token, cr_id)

        auth_key = extract_auth_key(cr)
        print_invite(args.name, cr_id, auth_key)


if __name__ == "__main__":
    main()
