"""Talking to the StorEyes gateway: identity, work modes, punch push."""
import re
from datetime import datetime

import requests

from . import config
from . import device
from .util import isapi_fmt


def get_board_id():
    with open("/proc/cpuinfo") as f:
        for line in f:
            m = re.match(r"Serial\s*:\s*(\S+)", line)
            if m:
                return m.group(1)
    raise RuntimeError("Board serial not found in /proc/cpuinfo")


def headers() -> dict:
    h = {"Content-Type": "application/json", "X-DEVICE-ID": get_board_id()}
    if config.GATEWAY_API_KEY is not None:
        h["X-API-KEY"] = config.GATEWAY_API_KEY
    return h


def fetch_work_modes() -> list:
    r = requests.get(config.WORK_MODE_URL, headers=headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def push(users: dict, raw_users: list, events: list, entry: dict):
    creds = device.build_credentials(users, raw_users)

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
        if device.is_punch(e)
    ]

    if not employees and not punches:
        print("[GATEWAY] Nothing to push.")
        return

    body = {
        "timestamp": isapi_fmt(datetime.now(config.TZ)),
        "target": {"shiftId": entry["shiftId"], "method": entry["method"]},
        "employees": employees,
        "punches": punches,
    }

    r = requests.post(config.PUNCH_URL, json=body, headers=headers(), timeout=10)
    if r.status_code in (200, 204):
        print(r.json())
        print(f"[GATEWAY] OK — {len(employees)} employees, {len(punches)} punches")
    else:
        print(f"[GATEWAY] ERROR {r.status_code}: {r.text}")
