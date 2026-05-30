"""Seccomp profile synthesizer for security policy generation."""


def synthesize_seccomp(raw_observations: dict[str, list[str]]) -> dict:
    """Merge per-asset syscall lists into a single seccomp allowlist profile."""
    all_syscalls: set[str] = set()
    for syscalls in raw_observations.values():
        all_syscalls.update(syscalls)
    return {
        "defaultAction": "SCMP_ACT_ERRNO",
        "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
        "syscalls": [{"names": sorted(all_syscalls), "action": "SCMP_ACT_ALLOW"}],
    }


def compute_delta(prior: dict, current: dict) -> dict:
    """Return {added, removed} syscall sets between two seccomp profiles."""
    prior_set = set(prior["syscalls"][0]["names"])
    current_set = set(current["syscalls"][0]["names"])
    return {
        "added": sorted(current_set - prior_set),
        "removed": sorted(prior_set - current_set),
    }
