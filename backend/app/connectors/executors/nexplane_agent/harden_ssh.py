from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "harden_ssh",
        "settings_applied": {
            "PermitRootLogin": "no",
            "PasswordAuthentication": "no",
            "PubkeyAuthentication": "yes",
            "MaxAuthTries": "4",
        },
        "drop_in_path": "/etc/ssh/sshd_config.d/99-nexplane-hardening.conf",
        "sshd_config_snapshot": {"/etc/ssh/sshd_config": "# previous sshd_config"},
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "harden_ssh"}
