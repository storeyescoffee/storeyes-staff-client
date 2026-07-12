import os
import re
import sys
import argparse
import configparser
import getpass
import subprocess
import requests
from requests.auth import HTTPDigestAuth
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path

_HERE = Path(__file__).parent.resolve()

CONFIG_FILE = _HERE / "config.conf"
DEFAULT_PUNCH_URL = "https://api.storeyes.io/api/staff/employee-logs/punch"
DEFAULT_WORK_MODE_URL = "https://api.storeyes.io/api/staff/work-mode"

# Read leniently: --configure has to work with no config.conf on disk yet.
_cfg = configparser.ConfigParser()
_cfg.read(CONFIG_FILE)

DEVICE_MAC = _cfg.get("device", "mac", fallback="").strip().lower()
AUTH = HTTPDigestAuth(
    _cfg.get("device", "username", fallback=""),
    _cfg.get("device", "password", fallback=""),
)

PUNCH_URL = _cfg.get("gateway", "punch_url", fallback=DEFAULT_PUNCH_URL)
WORK_MODE_URL = _cfg.get("gateway", "work_mode_url", fallback=DEFAULT_WORK_MODE_URL)
GATEWAY_API_KEY = _cfg.get("gateway", "api_key", fallback="") or None

SCHEDULE_FILE = _HERE / "schedule"
CRON_FILE = Path("/etc/cron.d/staff-schedule")
CRON_LOG = "/var/log/staff-schedule.log"
GRACE_MINUTES = 15  # added on top of each shift's tolerantLate for the IN deadline

TZ = ZoneInfo("Africa/Casablanca")


def _isapi_fmt(dt: datetime) -> str:
    """Format a tz-aware datetime as Hikvision expects: 2026-07-06T00:00:00+01:00."""
    s = dt.strftime("%Y-%m-%dT%H:%M:%S%z")  # -> +0100
    return s[:-2] + ":" + s[-2:]            # -> +01:00


def day_bounds(d: date = None):
    """Return (start, end) ISAPI strings for the given local day (default today)."""
    d = d or datetime.now(TZ).date()
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=TZ)
    end = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=TZ)
    return _isapi_fmt(start), _isapi_fmt(end)


def get_board_id():
    with open("/proc/cpuinfo") as f:
        for line in f:
            m = re.match(r"Serial\s*:\s*(\S+)", line)
            if m:
                return m.group(1)
    raise RuntimeError("Board serial not found in /proc/cpuinfo")


# major -> minor -> (label, success)
EVENT_MAP = {
    5: {
        38: ("Fingerprint", True),
        39: ("Fingerprint Failed", False),
        40: ("Card", True),
        41: ("Card Failed", False),
        43: ("Password", True),
        44: ("Password Failed", False),
        75: ("Lockout", False),
        1:  ("Local Login", None),
        2:  ("Local Logout", None),
        19: ("Remote Login", None),
        20: ("Remote Logout", None),
        21: ("Door Opened", None),
        22: ("Door Closed", None),
        49: ("Remote Unlock", None),
    },
    3: {
        112: ("Door Locked", None),
        113: ("Door Unlocked", None),
        16:  ("Door Held Open", False),
        17:  ("Door Forced Open", False),
    },
    1: {
        20: ("Door Open Button", None),
        32: ("Door Held Open", False),
        33: ("Door Forced Open", False),
    },
    6: {
        1:  ("Device Start", None),
        2:  ("Device Shutdown", None),
        3:  ("Device Reboot", None),
        55: ("NTP Sync", None),
    },
}

# Successful access minors that count as a punch.
PUNCH_MINORS = {38}  # fingerprint only; add 40 (card) / 43 (PIN) if needed


def is_punch(e: dict) -> bool:
    """A punch is a successful access event with a real employee attached."""
    return (
        e.get("major") == 5
        and e.get("minor") in PUNCH_MINORS
        and bool(e.get("employeeNoString", "").strip())
    )


MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


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
    print(f"Configuring {CONFIG_FILE}\n")

    while True:
        mac = _prompt("Device MAC address", DEVICE_MAC).lower()
        if MAC_RE.match(mac):
            break
        print("  expected format aa:bb:cc:dd:ee:ff")

    username = _prompt("Device username", _cfg.get("device", "username", fallback="admin"))
    password = _prompt("Device password", _cfg.get("device", "password", fallback=""), secret=True)

    out = configparser.ConfigParser()
    out["device"] = {"mac": mac, "username": username, "password": password}
    out["gateway"] = {"punch_url": PUNCH_URL, "work_mode_url": WORK_MODE_URL}
    if GATEWAY_API_KEY:
        out["gateway"]["api_key"] = GATEWAY_API_KEY

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        out.write(f)
    CONFIG_FILE.chmod(0o600)  # holds the device password

    print(f"\n[CONFIG] written to {CONFIG_FILE}")
    print(f"  device     {mac} as {username}")
    print(f"  punch      {PUNCH_URL}")
    print(f"  work mode  {WORK_MODE_URL}")


_device_ip = None


def device_ip() -> str:
    """Find the device on the LAN by MAC. Cached for the life of the process."""
    global _device_ip
    if _device_ip is not None:
        return _device_ip

    if not DEVICE_MAC:
        raise RuntimeError(f"No device MAC in {CONFIG_FILE} — run with --configure first")

    cmd = f"sudo arp-scan --localnet | awk '/{DEVICE_MAC}/{{print $1; exit}}'"
    r = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=120)
    ip = r.stdout.strip()
    if not ip:
        raise RuntimeError(
            f"Device {DEVICE_MAC} not found on the local network "
            f"(arp-scan exit {r.returncode}: {r.stderr.strip()})"
        )

    _device_ip = ip
    print(f"[DEVICE] {DEVICE_MAC} → {ip}\n")
    return ip


def api_post(path, payload):
    url = f"http://{device_ip()}{path}?format=json"
    r = requests.post(url, json=payload, auth=AUTH, timeout=5)
    r.raise_for_status()
    return r.json()


def fetch_users():
    data = api_post("/ISAPI/AccessControl/UserInfo/Search", {
        "UserInfoSearchCond": {
            "searchID": "1",
            "searchResultPosition": 0,
            "maxResults": 50
        }
    })
    raw = data["UserInfoSearch"]["UserInfo"]
    return {u["employeeNo"]: u["name"] for u in raw}, raw


def fetch_card_owners() -> set:
    """employeeNos with at least one card enrolled (bulk search)."""
    try:
        data = api_post("/ISAPI/AccessControl/CardInfo/Search", {
            "CardInfoSearchCond": {
                "searchID": "1",
                "searchResultPosition": 0,
                "maxResults": 200
            }
        })
        infos = data.get("CardInfoSearch", {}).get("CardInfo", [])
        return {c["employeeNo"] for c in infos if c.get("employeeNo")}
    except Exception:
        return set()


def has_fingerprint(emp: str) -> bool:
    """Per-user FP check; firmware has no bulk fingerprint search."""
    try:
        data = api_post("/ISAPI/AccessControl/FingerPrintUpload", {
            "FingerPrintCond": {
                "searchID": "1",
                "employeeNo": emp,
                "cardReaderNo": 1
            }
        })
        fps = data.get("FingerPrintInfo", {}).get("FingerPrintList", [])
        return len(fps) > 0
    except Exception:
        return False


def build_credentials(users: dict, raw_users: list) -> dict:
    """employeeNo -> ["BADGE", "PIN", "FINGERPRINT"]"""
    card_owners = fetch_card_owners()
    pin_owners = {u["employeeNo"] for u in raw_users if u.get("password")}

    creds = {}
    for emp in users:
        c = []
        if emp in card_owners:
            c.append("BADGE")
        if emp in pin_owners:
            c.append("PIN")
        if has_fingerprint(emp):
            c.append("FINGERPRINT")
        creds[emp] = c
    return creds


def fetch_events(start, end):
    events = []
    position = 0
    while True:
        data = api_post("/ISAPI/AccessControl/AcsEvent", {
            "AcsEventCond": {
                "searchID": "1",
                "searchResultPosition": position,
                "maxResults": 30,
                "major": 0,
                "minor": 0,
                "startTime": start,
                "endTime": end
            }
        })
        acs = data["AcsEvent"]
        events.extend(acs.get("InfoList", []))
        if acs["responseStatusStrg"] != "MORE":
            break
        position += 30
    return events


def sync_time():
    now = datetime.now(TZ)
    time_str = _isapi_fmt(now)
    offset_h = int(now.utcoffset().total_seconds() // 3600)
    # Hikvision CST format is inverted: UTC+1 -> "CST-1:00:00"
    tz_str = f"CST{-offset_h}:00:00"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Time>
  <timeMode>manual</timeMode>
  <localTime>{time_str}</localTime>
  <timeZone>{tz_str}</timeZone>
</Time>"""
    r = requests.put(
        f"http://{device_ip()}/ISAPI/System/time",
        data=xml,
        auth=AUTH,
        headers={"Content-Type": "application/xml"},
        timeout=5
    )
    r.raise_for_status()
    print(f"[TIME SYNC] Device time set to {time_str} ({tz_str})\n")


def resolve_event(major, minor):
    label, success = EVENT_MAP.get(major, {}).get(minor, (f"Event(major={major},minor={minor})", None))
    return label, success


def gateway_headers() -> dict:
    headers = {"Content-Type": "application/json", "X-DEVICE-ID": get_board_id()}
    if GATEWAY_API_KEY is not None:
        headers["X-API-KEY"] = GATEWAY_API_KEY
    return headers


def fetch_work_modes() -> list:
    r = requests.get(WORK_MODE_URL, headers=gateway_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def _shift_hhmm(t: str, plus_minutes: int = 0) -> str:
    """'06:30:00' + minutes -> 'HH:MM', wrapping past midnight."""
    h, m = int(t[:2]), int(t[3:5])
    total = (h * 60 + m + plus_minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def write_schedule(work_modes: list) -> list:
    """Write one line per shift boundary: <n>,<shift_id>,<IN|OUT>,<time>

    IN closes at startTime + tolerantLate + GRACE_MINUTES; OUT is endTime as-is.
    """
    lines = []
    for wm in work_modes:
        in_time = _shift_hhmm(wm["startTime"], (wm.get("tolerantLate") or 0) + GRACE_MINUTES)
        out_time = _shift_hhmm(wm["endTime"])
        for method, hhmm in (("IN", in_time), ("OUT", out_time)):
            lines.append(f"{len(lines) + 1},{wm['id']},{method},{hhmm}")

    SCHEDULE_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def ensure_root():
    """Re-exec the whole command under sudo; /etc/cron.d needs root."""
    if os.geteuid() == 0:
        return
    print("[SUDO] re-running as root to write /etc/cron.d ...")
    os.execvp("sudo", ["sudo", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def write_cronfile(schedule_lines: list) -> str:
    """Render /etc/cron.d/staff-schedule: one job per schedule line, at its time."""
    python = sys.executable or "/usr/bin/python3"
    script = Path(__file__).resolve()

    out = [
        "# Managed by main.py --sync - do not edit; regenerated on every sync.",
        "SHELL=/bin/sh",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        f"CRON_TZ={TZ.key}",
        "",
    ]
    for line in schedule_lines:
        n, shift_id, method, hhmm = line.split(",")
        hh, mm = hhmm.split(":")
        out.append(
            f"{int(mm)} {int(hh)} * * * root {python} {script} {n} "
            f">> {CRON_LOG} 2>&1  # {method} {shift_id}"
        )

    content = "\n".join(out) + "\n"
    CRON_FILE.write_text(content, encoding="utf-8")
    CRON_FILE.chmod(0o644)  # cron ignores cron.d files that are group/world-writable
    return content


def read_schedule_entry(schedule_id: int) -> dict:
    """Resolve a schedule-file line number to its shift id, method and time."""
    if not SCHEDULE_FILE.exists():
        raise RuntimeError(f"{SCHEDULE_FILE} not found — run with --sync first")

    for line in SCHEDULE_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        n, shift_id, method, time_str = line.split(",")
        if int(n) == schedule_id:
            return {"shiftId": shift_id, "method": method, "time": time_str}

    raise RuntimeError(f"No schedule entry with id {schedule_id} in {SCHEDULE_FILE}")


def sync_work_modes():
    ensure_root()

    work_modes = fetch_work_modes()
    print(f"[SYNC] {len(work_modes)} work mode(s) from {WORK_MODE_URL}")
    for wm in work_modes:
        print(f"  {wm['name']}: {wm['startTime']} → {wm['endTime']}")

    lines = write_schedule(work_modes)
    print(f"\n[SCHEDULE] {len(lines)} line(s) written to {SCHEDULE_FILE}")
    for line in lines:
        print(f"  {line}")

    content = write_cronfile(lines)
    print(f"\n[CRON] {len(lines)} job(s) written to {CRON_FILE}")
    print("".join(f"  {l}\n" for l in content.splitlines() if l and not l.startswith("#")))


def push_to_gateway(users: dict, raw_users: list, events: list, entry: dict):
    headers = gateway_headers()

    creds = build_credentials(users, raw_users)

    employees = [
        {"name": name, "code": code, "credentials": creds.get(code, [])}
        for code, name in users.items()
    ]

    punches = [
        {
            "employeeCode": e["employeeNoString"].strip(),
            "time": e["time"][11:19],  # "HH:MM:SS" from ISO timestamp
        }
        for e in events
        if is_punch(e)
    ]

    if not employees and not punches:
        print("[GATEWAY] Nothing to push.")
        return

    body = {
        "timestamp": _isapi_fmt(datetime.now(TZ)),
        "target": {"shiftId": entry["shiftId"], "method": entry["method"]},
        "employees": employees,
        "punches": punches,
    }

    r = requests.post(PUNCH_URL, json=body, headers=headers, timeout=10)
    if r.status_code in (200, 204):
        print(r.json())
        print(f"[GATEWAY] OK — {len(employees)} employees, {len(punches)} punches")
    else:
        print(f"[GATEWAY] ERROR {r.status_code}: {r.text}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "schedule_id",
        nargs="?",
        type=int,
        help="line number in the schedule file identifying the shift boundary to push",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="fetch work modes from the gateway and rewrite the schedule file",
    )
    parser.add_argument(
        "--configure",
        action="store_true",
        help="interactively write config.conf (device MAC, username, password)",
    )
    args = parser.parse_args()

    if args.configure:
        configure()
        return

    if args.sync:
        sync_work_modes()
        return

    if args.schedule_id is None:
        parser.error("schedule_id is required (or use --sync / --configure)")

    entry = read_schedule_entry(args.schedule_id)
    print(f"[SCHEDULE] #{args.schedule_id} → shift {entry['shiftId']} "
          f"{entry['method']} @ {entry['time']}\n")

    sync_time()

    # Users
    users, raw_users = fetch_users()
    print(f"[USERS] {len(users)} registered")
    for emp, name in users.items():
        print(f"  {emp} → {name}")

    # Events — bounds are timezone-aware (Africa/Casablanca)
    today = datetime.now(TZ).date().isoformat()
    start, end = day_bounds()
    events = fetch_events(start, end)

    # Safety net: keep only events whose local date is actually today,
    # in case the device interprets the boundary differently.
    events = [e for e in events if e.get("time", "")[:10] == today]

    print(f"\n[EVENTS] {len(events)} total ({start} → {end})\n")

    print(f"{'Time':<26} {'Name':<22} {'Event':<25} {'Status'}")
    print("-" * 85)

    for e in events:
        major = e.get("major", 0)
        minor = e.get("minor", 0)
        emp = e.get("employeeNoString", "").strip()
        name = users.get(emp, "—") if emp else "—"
        label, success = resolve_event(major, minor)

        if success is True:
            status = "✅"
        elif success is False:
            status = "❌"
        else:
            status = "ℹ️"

        print(f"{e['time']:<26} {name:<22} {label:<25} {status}")

    push_to_gateway(users, raw_users, events, entry)


if __name__ == "__main__":
    main()