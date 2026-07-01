# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Reverse-tunnel destination authorizer (deny-by-default allowlist).

A tunnel-enabled agent carries an allowlist of destinations it may be asked to
reach. Every dial is checked against it **at the control plane** (here) before a
stream is opened, and again **at the agent** before it dials locally
(belt-and-suspenders). Anything not explicitly allowed is refused.

Allowlist entries (strings):
  "10.0.0.0/8:5432"        CIDR + single port
  "10.0.0.0/24:1-1024"     CIDR + inclusive port range
  "192.168.1.10:443"       single host (IP) + port
  "db.internal:5432"       hostname (exact match) + port
  "10.0.0.0/8:*"           CIDR + any port

Host in a dial may be an IP or a hostname. IP dials are matched against CIDR /
IP rules; hostname dials are matched against hostname rules (exact,
case-insensitive). Hostname->IP resolution and its re-check happen at the agent.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass


class AllowlistError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    # exactly one of network / hostname is set
    network: ipaddress._BaseNetwork | None
    hostname: str | None
    port_lo: int
    port_hi: int

    def matches(self, host: str, port: int) -> bool:
        if not (self.port_lo <= port <= self.port_hi):
            return False
        ip = _as_ip(host)
        if self.network is not None:
            return ip is not None and ip in self.network
        # hostname rule: only matches non-IP hosts, case-insensitive exact
        return ip is None and self.hostname == host.lower()


def _as_ip(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _parse_ports(spec: str) -> tuple[int, int]:
    if spec == "*":
        return 1, 65535
    if "-" in spec:
        lo_s, hi_s = spec.split("-", 1)
        lo, hi = int(lo_s), int(hi_s)
    else:
        lo = hi = int(spec)
    if not (1 <= lo <= hi <= 65535):
        raise AllowlistError(f"invalid port spec {spec!r}")
    return lo, hi


def parse_rule(entry: str) -> Rule:
    entry = entry.strip()
    host_part, sep, port_part = entry.rpartition(":")
    if not sep or not host_part:
        raise AllowlistError(f"allowlist entry must be 'host:port': {entry!r}")
    lo, hi = _parse_ports(port_part)
    # CIDR or bare IP -> network rule; otherwise hostname rule.
    try:
        net = ipaddress.ip_network(host_part, strict=False)
        return Rule(network=net, hostname=None, port_lo=lo, port_hi=hi)
    except ValueError:
        return Rule(network=None, hostname=host_part.lower(), port_lo=lo, port_hi=hi)


def parse_allowlist(entries: list[str]) -> list[Rule]:
    return [parse_rule(e) for e in entries if e and e.strip()]


def is_allowed(allowlist: list[Rule], host: str, port: int) -> bool:
    """Deny-by-default: True only if some rule matches."""
    if not isinstance(port, int) or not (1 <= port <= 65535):
        return False
    return any(r.matches(host, port) for r in allowlist)
