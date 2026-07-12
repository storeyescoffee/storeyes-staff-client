"""Entrypoint: push one shift boundary's users + punches to the gateway.

  main.py --configure [--ip]   write config.conf
  main.py --sync               refresh the schedule file and cron jobs
  main.py <schedule_id>        run the shift boundary on that schedule line
"""
import argparse
from datetime import datetime

from src import config
from src import device
from src import gateway
from src.configure import configure
from src.sync import read_schedule_entry, sync_work_modes
from src.util import day_bounds


def run(schedule_id: int):
    entry = read_schedule_entry(schedule_id)
    print(f"[SCHEDULE] #{schedule_id} → shift {entry['shiftId']} "
          f"{entry['method']} @ {entry['time']}\n")

    device.sync_time()

    # Users
    users, raw_users = device.fetch_users()
    print(f"[USERS] {len(users)} registered")
    for emp, name in users.items():
        print(f"  {emp} → {name}")

    # Events — bounds are timezone-aware (Africa/Casablanca)
    today = datetime.now(config.TZ).date().isoformat()
    start, end = day_bounds()
    events = device.fetch_events(start, end)

    # Safety net: keep only events whose local date is actually today,
    # in case the device interprets the boundary differently.
    events = [e for e in events if e.get("time", "")[:10] == today]

    print(f"\n[EVENTS] {len(events)} total ({start} → {end})\n")

    print(f"{'Time':<26} {'Name':<22} {'Event':<25} {'Status'}")
    print("-" * 85)

    for e in events:
        emp = e.get("employeeNoString", "").strip()
        name = users.get(emp, "—") if emp else "—"
        label, success = device.resolve_event(e.get("major", 0), e.get("minor", 0))

        if success is True:
            status = "✅"
        elif success is False:
            status = "❌"
        else:
            status = "ℹ️"

        print(f"{e['time']:<26} {name:<22} {label:<25} {status}")

    gateway.push(users, raw_users, events, entry)


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
    parser.add_argument(
        "--ip",
        action="store_true",
        help="with --configure, also prompt for the device IP "
             "(otherwise it is discovered by MAC and cached)",
    )
    args = parser.parse_args()

    if args.configure:
        configure(ask_ip=args.ip)
        return

    if args.ip:
        parser.error("--ip only makes sense together with --configure")

    if args.sync:
        sync_work_modes()
        return

    if args.schedule_id is None:
        parser.error("schedule_id is required (or use --sync / --configure)")

    run(args.schedule_id)


if __name__ == "__main__":
    main()
