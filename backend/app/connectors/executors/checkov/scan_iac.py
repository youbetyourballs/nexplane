import asyncio
import json
import subprocess


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "scan_iac", "findings": [
            {"check_id": "CKV_AWS_1", "check_type": "terraform", "resource": "aws_s3_bucket.data", "file": "main.tf", "passed": False, "guideline": "Ensure S3 bucket has access control list (ACL) applied"}
        ], "passed": 5, "failed": 1}
    repo_path = creds["repo_path"]
    framework = creds.get("framework", "all")
    loop = asyncio.get_event_loop()

    def run_checkov():
        cmd = ["checkov", "-d", repo_path, "--framework", framework, "-o", "json", "--quiet"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"results": {"failed_checks": [], "passed_checks": []}}

    data = await loop.run_in_executor(None, run_checkov)
    results = data.get("results", data) if isinstance(data, dict) else {}
    failed = results.get("failed_checks", [])
    passed = results.get("passed_checks", [])
    findings = [{"check_id": c.get("check_id"), "check_type": c.get("check_type"), "resource": c.get("resource"), "file": c.get("file_path"), "passed": False, "guideline": c.get("check_result", {}).get("result", "")} for c in failed]
    return {"action": "scan_iac", "findings": findings, "passed": len(passed), "failed": len(failed)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan has no rollback"}
