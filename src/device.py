"""Hikvision device: the ISAPI calls."""
from datetime import datetime

import requests

from . import config
from .util import isapi_fmt

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


def resolve_event(major, minor):
    label, success = EVENT_MAP.get(major, {}).get(minor, (f"Event(major={major},minor={minor})", None))
    return label, success


# --- ISAPI -------------------------------------------------------------------

def device_ip() -> str:
    if not config.DEVICE_IP:
        raise RuntimeError(f"No device IP in {config.CONFIG_FILE} — run with --configure first")
    return config.DEVICE_IP


def api_post(path, payload):
    url = f"http://{device_ip()}{path}?format=json"
    r = requests.post(url, json=payload, auth=config.AUTH, timeout=5)
    r.raise_for_status()
    return r.json()


def sync_time():
    now = datetime.now(config.TZ)
    time_str = isapi_fmt(now)
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
        auth=config.AUTH,
        headers={"Content-Type": "application/xml"},
        timeout=5
    )
    r.raise_for_status()
    print(f"[TIME SYNC] Device time set to {time_str} ({tz_str})\n")


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
