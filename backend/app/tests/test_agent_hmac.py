from app.services.agent_hmac import compute_job_signature, verify_job_signature


def test_compute_signature_is_deterministic():
    sig1 = compute_job_signature("secret", "job-1", "change_ip", {"interface": "eth0"})
    sig2 = compute_job_signature("secret", "job-1", "change_ip", {"interface": "eth0"})
    assert sig1 == sig2


def test_signature_varies_by_secret():
    sig1 = compute_job_signature("secret-a", "job-1", "change_ip", {})
    sig2 = compute_job_signature("secret-b", "job-1", "change_ip", {})
    assert sig1 != sig2


def test_signature_varies_by_command():
    sig1 = compute_job_signature("secret", "job-1", "change_ip", {})
    sig2 = compute_job_signature("secret", "job-1", "configure_syslog", {})
    assert sig1 != sig2


def test_verify_returns_true_for_correct_signature():
    sig = compute_job_signature("secret", "job-1", "change_ip", {"interface": "eth0"})
    assert verify_job_signature("secret", "job-1", "change_ip", {"interface": "eth0"}, sig) is True


def test_verify_returns_false_for_wrong_signature():
    assert verify_job_signature("secret", "job-1", "change_ip", {}, "badsig") is False


def test_canonical_json_sorts_keys():
    sig1 = compute_job_signature("s", "j", "cmd", {"b": 1, "a": 2})
    sig2 = compute_job_signature("s", "j", "cmd", {"a": 2, "b": 1})
    assert sig1 == sig2
