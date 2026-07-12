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


def _prompt_resolution() -> str:
    """How to find the device: a fixed IP, or a MAC we scan the LAN for."""
    print("How should the device be located?")
    print(f"  {config.RESOLUTION_IP:4} - by IP address (static / DHCP reservation)")
    print(f"  {config.RESOLUTION_MAC:4} - by MAC address (scan the LAN, cache the IP)")
    while True:
        choice = _prompt("Resolution", config.RESOLUTION).lower()
        if choice in config.RESOLUTIONS:
            print()
            return choice
        print(f"  expected one of {', '.join(config.RESOLUTIONS)}")


def _prompt_ip() -> str:
    while True:
        ip = _prompt("Device IP address", config.DEVICE_IP)
        try:
            ipaddress.ip_address(ip)
            return ip
        except ValueError:
            print("  not a valid IP address")


def _prompt_mac() -> str:
    while True:
        mac = _prompt("Device MAC address", config.DEVICE_MAC).lower()
        if config.MAC_RE.match(mac):
            return mac
        print("  expected format aa:bb:cc:dd:ee:ff")


def configure():
    """Interactively (re)write config.conf."""
    cfg = config.cfg
    print(f"Configuring {config.CONFIG_FILE}\n")

    resolution = _prompt_resolution()
    ip = mac = ""
    if resolution == config.RESOLUTION_IP:
        ip = _prompt_ip()
    else:
        mac = _prompt_mac()

    username = _prompt("Device username", cfg.get("device", "username", fallback="admin"))
    password = _prompt("Device password", cfg.get("device", "password", fallback=""), secret=True)

    if not cfg.has_section("device"):
        cfg.add_section("device")
    cfg["device"]["resolution"] = resolution
    cfg["device"]["username"] = username
    cfg["device"]["password"] = password

    # Only the key the chosen mode reads survives; a stale one would be a lie about
    # which device we talk to. Under `mac` the IP comes back as a discovery cache.
    if resolution == config.RESOLUTION_IP:
        cfg["device"]["ip"] = ip
        cfg.remove_option("device", "mac")
    else:
        cfg["device"]["mac"] = mac
        if mac != config.DEVICE_MAC:
            cfg.remove_option("device", "ip")  # cached IP belonged to the previous device

    if not cfg.has_section("gateway"):
        cfg.add_section("gateway")
    cfg["gateway"]["punch_url"] = config.PUNCH_URL
    cfg["gateway"]["work_mode_url"] = config.WORK_MODE_URL
    if config.GATEWAY_API_KEY:
        cfg["gateway"]["api_key"] = config.GATEWAY_API_KEY

    config.write()

    print(f"\n[CONFIG] written to {config.CONFIG_FILE}")
    print(f"  device     {ip or mac} as {username} (by {resolution})")
    print(f"  punch      {config.PUNCH_URL}")
    print(f"  work mode  {config.WORK_MODE_URL}")
