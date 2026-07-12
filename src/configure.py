"""--configure: interactively write config.conf."""
import getpass
import ipaddress

from . import config


def _prompt(label: str, current: str = "", secret: bool = False) -> str:
    """Ask until non-empty; Enter keeps the current value when there is one."""
    hint = " [keep current]" if (current and secret) else (f" [{current}]" if current else "")
    ask = getpass.getpass if secret else input
    while True:
        value = ask(f"{label}{hint}: ").strip() or current
        if value:
            return value
        print("  value is required")


def configure():
    """Interactively (re)write config.conf."""
    cfg = config.cfg
    print(f"Configuring {config.CONFIG_FILE}\n")

    while True:
        ip = _prompt("Device IP address", config.DEVICE_IP)
        try:
            ipaddress.ip_address(ip)
            break
        except ValueError:
            print("  not a valid IP address")

    username = _prompt("Device username", cfg.get("device", "username", fallback="admin"))
    password = _prompt("Device password", cfg.get("device", "password", fallback=""), secret=True)

    if not cfg.has_section("device"):
        cfg.add_section("device")
    cfg["device"]["ip"] = ip
    cfg["device"]["username"] = username
    cfg["device"]["password"] = password

    if not cfg.has_section("gateway"):
        cfg.add_section("gateway")
    cfg["gateway"]["punch_url"] = config.PUNCH_URL
    cfg["gateway"]["work_mode_url"] = config.WORK_MODE_URL
    if config.GATEWAY_API_KEY:
        cfg["gateway"]["api_key"] = config.GATEWAY_API_KEY

    config.write()

    print(f"\n[CONFIG] written to {config.CONFIG_FILE}")
    print(f"  device     {ip} as {username}")
    print(f"  punch      {config.PUNCH_URL}")
    print(f"  work mode  {config.WORK_MODE_URL}")
