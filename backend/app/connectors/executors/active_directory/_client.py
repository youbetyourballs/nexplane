from ldap3 import Server, Connection, ALL


def get_connection(creds: dict) -> Connection:
    use_ssl = str(creds.get("use_ssl", "false")).lower() == "true"
    port = int(creds.get("port", 636 if use_ssl else 389))
    server = Server(creds["server"], port=port, use_ssl=use_ssl, get_info=ALL)
    return Connection(server, user=creds["bind_dn"], password=creds["bind_password"], auto_bind=True)
