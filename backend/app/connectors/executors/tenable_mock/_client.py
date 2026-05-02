def get_tio(creds: dict):
    from tenable.io import TenableIO
    return TenableIO(access_key=creds["access_key"], secret_key=creds["secret_key"])
