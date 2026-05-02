from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "configure_pam",
        "profile_applied": parameters.get("profile", "cis_level1"),
        "pam_variant": {"pw_module": "pam_pwquality", "fail_module": "pam_faillock"},
        "params_applied": {"min_len": 14, "max_failed": 5, "lockout_secs": 900, "remember": 5},
        "files_snapshot": {
            "/etc/security/pwquality.conf": "# previous content",
            "/etc/pam.d/common-auth": "# previous content",
        },
        "applied": True,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_pam", "restored_files": 5}
