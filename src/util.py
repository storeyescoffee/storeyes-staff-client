"""Time formatting shared by the device (ISAPI) and gateway sides."""
from datetime import datetime, date

from . import config


def isapi_fmt(dt: datetime) -> str:
    """Format a tz-aware datetime as Hikvision expects: 2026-07-06T00:00:00+01:00."""
    s = dt.strftime("%Y-%m-%dT%H:%M:%S%z")  # -> +0100
    return s[:-2] + ":" + s[-2:]            # -> +01:00


def day_bounds(d: date = None):
    """Return (start, end) ISAPI strings for the given local day (default today)."""
    d = d or datetime.now(config.TZ).date()
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=config.TZ)
    end = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=config.TZ)
    return isapi_fmt(start), isapi_fmt(end)
