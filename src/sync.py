"""--sync: pull work modes from the gateway into the schedule file and cron."""
import os
import sys

from . import config
from . import gateway


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
        in_time = _shift_hhmm(wm["startTime"], (wm.get("tolerantLate") or 0) + config.GRACE_MINUTES)
        out_time = _shift_hhmm(wm["endTime"])
        for method, hhmm in (("IN", in_time), ("OUT", out_time)):
            lines.append(f"{len(lines) + 1},{wm['id']},{method},{hhmm}")

    config.SCHEDULE_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def read_schedule_entry(schedule_id: int) -> dict:
    """Resolve a schedule-file line number to its shift id, method and time."""
    if not config.SCHEDULE_FILE.exists():
        raise RuntimeError(f"{config.SCHEDULE_FILE} not found — run with --sync first")

    for line in config.SCHEDULE_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        n, shift_id, method, time_str = line.split(",")
        if int(n) == schedule_id:
            return {"shiftId": shift_id, "method": method, "time": time_str}

    raise RuntimeError(f"No schedule entry with id {schedule_id} in {config.SCHEDULE_FILE}")


def ensure_root():
    """Re-exec the whole command under sudo; /etc/cron.d needs root."""
    if os.geteuid() == 0:
        return
    print("[SUDO] re-running as root to write /etc/cron.d ...")
    os.execvp("sudo", ["sudo", sys.executable, str(config.ENTRYPOINT), *sys.argv[1:]])


def write_cronfile(schedule_lines: list) -> str:
    """Render /etc/cron.d/staff-schedule: one job per schedule line, at its time."""
    python = sys.executable or "/usr/bin/python3"

    out = [
        "# Managed by main.py --sync - do not edit; regenerated on every sync.",
        "SHELL=/bin/sh",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        f"CRON_TZ={config.TZ.key}",
        "",
    ]
    for line in schedule_lines:
        n, shift_id, method, hhmm = line.split(",")
        hh, mm = hhmm.split(":")
        out.append(
            f"{int(mm)} {int(hh)} * * * root {python} {config.ENTRYPOINT} {n} "
            f">> {config.CRON_LOG} 2>&1  # {method} {shift_id}"
        )

    content = "\n".join(out) + "\n"
    config.CRON_FILE.write_text(content, encoding="utf-8")
    config.CRON_FILE.chmod(0o644)  # cron ignores cron.d files that are group/world-writable
    return content


def sync_work_modes():
    ensure_root()

    work_modes = gateway.fetch_work_modes()
    print(f"[SYNC] {len(work_modes)} work mode(s) from {config.WORK_MODE_URL}")
    for wm in work_modes:
        print(f"  {wm['name']}: {wm['startTime']} → {wm['endTime']}")

    lines = write_schedule(work_modes)
    print(f"\n[SCHEDULE] {len(lines)} line(s) written to {config.SCHEDULE_FILE}")
    for line in lines:
        print(f"  {line}")

    content = write_cronfile(lines)
    print(f"\n[CRON] {len(lines)} job(s) written to {config.CRON_FILE}")
    print("".join(f"  {l}\n" for l in content.splitlines() if l and not l.startswith("#")))
