"""Config file access: load, expose, persist."""
import re
import configparser
from pathlib import Path
from zoneinfo import ZoneInfo
from requests.auth import HTTPDigestAuth

ROOT = Path(__file__).resolve().parent.parent  # src/ -> project root

CONFIG_FILE = ROOT / "config.conf"
ENTRYPOINT = ROOT / "main.py"  # what the cron jobs invoke
SCHEDULE_FILE = ROOT / "schedule"
CRON_FILE = Path("/etc/cron.d/staff-schedule")
CRON_LOG = "/var/log/staff-schedule.log"

GRACE_MINUTES = 15  # added on top of each shift's tolerantLate for the IN deadline
TZ = ZoneInfo("Africa/Casablanca")

DEFAULT_PUNCH_URL = "https://api.storeyes.io/api/staff/employee-logs/punch"
DEFAULT_WORK_MODE_URL = "https://api.storeyes.io/api/staff/work-mode"

MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")

# Read leniently: --configure has to work with no config.conf on disk yet.
cfg = configparser.ConfigParser()
cfg.read(CONFIG_FILE)

# Read these through the module (config.DEVICE_IP), not `from config import ...`:
# save_device_ip() rebinds DEVICE_IP, and a from-import would keep the stale value.
DEVICE_MAC = cfg.get("device", "mac", fallback="").strip().lower()
DEVICE_IP = cfg.get("device", "ip", fallback="").strip()  # cache; refreshed on miss
AUTH = HTTPDigestAuth(
    cfg.get("device", "username", fallback=""),
    cfg.get("device", "password", fallback=""),
)

PUNCH_URL = cfg.get("gateway", "punch_url", fallback=DEFAULT_PUNCH_URL)
WORK_MODE_URL = cfg.get("gateway", "work_mode_url", fallback=DEFAULT_WORK_MODE_URL)
GATEWAY_API_KEY = cfg.get("gateway", "api_key", fallback="") or None


def write():
    """Persist the in-memory config back to config.conf."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        cfg.write(f)
    CONFIG_FILE.chmod(0o600)  # holds the device password


def save_device_ip(ip: str):
    """Cache a freshly discovered IP so the next run can skip the scan."""
    global DEVICE_IP
    if not cfg.has_section("device"):
        cfg.add_section("device")
    cfg["device"]["ip"] = ip
    DEVICE_IP = ip
    write()
