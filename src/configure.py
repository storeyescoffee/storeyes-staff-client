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


def configure(ask_ip: bool = False):
    """Interactively (re)write config.conf.

    The IP is normally discovered by MAC and only prompted for with --ip, e.g.
    when arp-scan can't see the device (different subnet, scan blocked).
    """
    cfg = config.cfg
    print(f"Configuring {config.CONFIG_FILE}\n")

    while True:
        mac = _prompt("Device MAC address", config.DEVICE_MAC).lower()
        if config.MAC_RE.match(mac):
            break
        print("  expected format aa:bb:cc:dd:ee:ff")

    ip = ""
    while ask_ip:
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
    cfg["device"]["mac"] = mac
    cfg["device"]["username"] = username
    cfg["device"]["password"] = password
    if mac != config.DEVICE_MAC:
        cfg.remove_option("device", "ip")  # cached IP belonged to the previous device
    if ip:
        cfg["device"]["ip"] = ip

    if not cfg.has_section("gateway"):
        cfg.add_section("gateway")
    cfg["gateway"]["punch_url"] = config.PUNCH_URL
    cfg["gateway"]["work_mode_url"] = config.WORK_MODE_URL
    if config.GATEWAY_API_KEY:
        cfg["gateway"]["api_key"] = config.GATEWAY_API_KEY

    config.write()

    print(f"\n[CONFIG] written to {config.CONFIG_FILE}")
    print(f"  device     {mac} as {username}")
    print(f"  ip         {ip or cfg.get('device', 'ip', fallback='(discovered by MAC)')}")
    print(f"  punch      {config.PUNCH_URL}")
    print(f"  work mode  {config.WORK_MODE_URL}")
