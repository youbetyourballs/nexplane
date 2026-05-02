import io
import paramiko

ALLOWED_PREFIXES = (
    "systemctl status",
    "systemctl list-units",
    "systemctl is-active",
    "journalctl",
    "tail -n",
    "df -h",
    "df -",
    "free -h",
    "free -",
    "uptime",
    "uname -",
    "hostname",
    "cat /etc/os-release",
    "ps aux",
)


def get_ssh_client(creds: dict) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {
        "hostname": creds["hostname"],
        "port": int(creds.get("port", 22)),
        "username": creds["username"],
        "timeout": 30,
    }
    if creds.get("private_key"):
        kwargs["pkey"] = paramiko.RSAKey.from_private_key(io.StringIO(creds["private_key"]))
    elif creds.get("password"):
        kwargs["password"] = creds["password"]
    client.connect(**kwargs)
    return client


def is_allowed(cmd: str) -> bool:
    return any(cmd.strip().startswith(p) for p in ALLOWED_PREFIXES)
